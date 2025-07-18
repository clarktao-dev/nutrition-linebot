import os
import json
import sqlite3
import re
import requests
import threading
import time

from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, ImageMessage, TextSendMessage,
    QuickReply, QuickReplyButton, MessageAction
)
from dotenv import load_dotenv

# 載入環境變數
load_dotenv()

app = Flask(__name__)

# LINE Bot 設定
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# 初始化
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 用戶狀態管理
user_states = {}

# 資料庫初始化
def init_db():
    conn = None
    try:
        conn = sqlite3.connect('nutrition_bot.db', timeout=20.0)
        cursor = conn.cursor()
    
        # 用戶資料表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                name TEXT,
                age INTEGER,
                gender TEXT,
                height REAL,
                weight REAL,
                activity_level TEXT,
                health_goals TEXT,
                dietary_restrictions TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                body_fat_percentage REAL DEFAULT 0,
                diabetes_type TEXT,
                target_calories REAL DEFAULT 2000,
                target_carbs REAL DEFAULT 250,
                target_protein REAL DEFAULT 100,
                target_fat REAL DEFAULT 70,
                bmr REAL DEFAULT 1500,
                tdee REAL DEFAULT 2000,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_reminder_sent TIMESTAMP,
                last_profile_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                visceral_fat_level INTEGER DEFAULT 0,
                muscle_mass REAL DEFAULT 0
            )
        ''')
        
        # 添加用戶表的新欄位
        user_columns = [
            ('body_fat_percentage', 'REAL DEFAULT 0'),
            ('diabetes_type', 'TEXT'),
            ('target_calories', 'REAL DEFAULT 2000'),
            ('target_carbs', 'REAL DEFAULT 250'),
            ('target_protein', 'REAL DEFAULT 100'),
            ('target_fat', 'REAL DEFAULT 70'),
            ('bmr', 'REAL DEFAULT 1500'),
            ('tdee', 'REAL DEFAULT 2000'),
            ('last_active', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'),
            ('last_reminder_sent', 'TIMESTAMP'),
            ('last_profile_update', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'),
            ('visceral_fat_level', 'INTEGER DEFAULT 0'),
            ('muscle_mass', 'REAL DEFAULT 0')
        ]


        
        for column_name, column_type in user_columns:
            try:
                cursor.execute(f'ALTER TABLE users ADD COLUMN {column_name} {column_type}')
                print(f"已添加用戶欄位：{column_name}")
            except sqlite3.OperationalError as e:
                if "duplicate column name" in str(e):
                    pass  # 欄位已存在，忽略
                else:
                    print(f"添加用戶欄位 {column_name} 時發生錯誤：{e}")
        
        # 添加新的營養素欄位（如果不存在）
        nutrition_columns = [
            ('calories', 'REAL DEFAULT 0'),
            ('carbs', 'REAL DEFAULT 0'),
            ('protein', 'REAL DEFAULT 0'),
            ('fat', 'REAL DEFAULT 0'),
            ('fiber', 'REAL DEFAULT 0'),
            ('sugar', 'REAL DEFAULT 0')
        ]

        for column_name, column_type in nutrition_columns:
            try:
                cursor.execute(f'ALTER TABLE meal_records ADD COLUMN {column_name} {column_type}')
                print(f"已添加欄位：{column_name}")
            except sqlite3.OperationalError as e:
                if "duplicate column name" in str(e):
                    print(f"欄位 {column_name} 已存在")
                else:
                    print(f"添加欄位 {column_name} 時發生錯誤：{e}")
        
        # 飲食記錄表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS meal_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                meal_type TEXT,
                meal_description TEXT,
                nutrition_analysis TEXT,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')

        # 每日營養總結表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_nutrition (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                date TEXT,
                total_calories REAL DEFAULT 0,
                total_carbs REAL DEFAULT 0,
                total_protein REAL DEFAULT 0,
                total_fat REAL DEFAULT 0,
                total_fiber REAL DEFAULT 0,
                total_sugar REAL DEFAULT 0,
                meal_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, date),
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')

        # 飲食偏好表（記錄用戶常吃的食物）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS food_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                food_item TEXT,
                frequency INTEGER DEFAULT 1,
                last_eaten TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        conn.commit()
        print("資料庫初始化成功")
        
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"資料庫初始化失敗：{e}")
    finally:
        if conn:
            conn.close()

# 初始化資料庫
init_db()

def get_user_data(user):
    """安全地從用戶資料中提取所需資訊"""
    if not user:
        return None
    
    return {
        'user_id': user[0],
        'name': user[1] if len(user) > 1 else "用戶",
        'age': user[2] if len(user) > 2 else 30,
        'gender': user[3] if len(user) > 3 else "未設定",
        'height': user[4] if len(user) > 4 else 170,
        'weight': user[5] if len(user) > 5 else 70,
        'activity_level': user[6] if len(user) > 6 else "中等活動量",
        'health_goals': user[7] if len(user) > 7 else "維持健康",
        'dietary_restrictions': user[8] if len(user) > 8 else "無",
        'created_at': user[9] if len(user) > 9 else None,
        'updated_at': user[10] if len(user) > 10 else None,
        'body_fat_percentage': user[11] if len(user) > 11 else 20.0,
        'diabetes_type': user[12] if len(user) > 12 else None,
        'target_calories': user[13] if len(user) > 13 else 2000.0,
        'target_carbs': user[14] if len(user) > 14 else 250.0,
        'target_protein': user[15] if len(user) > 15 else 100.0,
        'target_fat': user[16] if len(user) > 16 else 70.0,
        'bmr': user[17] if len(user) > 17 else 1500.0,
        'tdee': user[18] if len(user) > 18 else 2000.0,
        'last_active': user[19] if len(user) > 19 else None,
        'last_reminder_sent': user[20] if len(user) > 20 else None,
        'last_profile_update': user[21] if len(user) > 21 else None,
        'visceral_fat_level': user[22] if len(user) > 22 else 0,
        'muscle_mass': user[23] if len(user) > 23 else 0
    }



class UserManager:
    @staticmethod
    def get_user(user_id):
        conn = None
        try:
            conn = sqlite3.connect('nutrition_bot.db', timeout=10.0)
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
            user = cursor.fetchone()
            return user
        except Exception as e:
            print(f"取得用戶資料錯誤：{e}")
            return None
        finally:
            if conn:
                conn.close()
    
    @staticmethod
    def get_daily_nutrition(user_id, date=None):
        """取得每日營養總結"""
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        
        conn = None
        try:
            conn = sqlite3.connect('nutrition_bot.db', timeout=10.0)
            cursor = conn.cursor()
            
            print(f"🔍 DEBUG - 查詢每日營養：user_id={user_id}, date={date}")
            
            cursor.execute('''
                SELECT * FROM daily_nutrition WHERE user_id = ? AND date = ?
            ''', (user_id, date))
            result = cursor.fetchone()
            
            print(f"🔍 DEBUG - 查詢結果：{result}")
            
            return result
        except Exception as e:
            print(f"❌ 取得每日營養總結錯誤：{e}")
            return None
        finally:
            if conn:
                conn.close()


    @staticmethod
    def save_user(user_id, user_data):
        conn = sqlite3.connect('nutrition_bot.db')
        cursor = conn.cursor()
        
        # 計算基本 BMI 和預設營養目標
        height_m = user_data['height'] / 100
        bmi = user_data['weight'] / (height_m ** 2)
        
        # 簡單的熱量計算（可以後續改進）
        if user_data['gender'] == '男性':
            bmr = 88.362 + (13.397 * user_data['weight']) + (4.799 * user_data['height']) - (5.677 * user_data['age'])
        else:
            bmr = 447.593 + (9.247 * user_data['weight']) + (3.098 * user_data['height']) - (4.330 * user_data['age'])
        
        # 活動係數
        activity_multiplier = {'低活動量': 1.2, '中等活動量': 1.55, '高活動量': 1.9}
        tdee = bmr * activity_multiplier.get(user_data['activity_level'], 1.2)
        
        # 營養素分配 (碳水50%, 蛋白質20%, 脂肪30%)
        target_calories = tdee
        target_carbs = (tdee * 0.5) / 4  # 碳水1g = 4卡
        target_protein = (tdee * 0.2) / 4  # 蛋白質1g = 4卡
        target_fat = (tdee * 0.3) / 9  # 脂肪1g = 9卡
        
        cursor.execute('''
            INSERT OR REPLACE INTO users 
            (user_id, name, age, gender, height, weight, activity_level, health_goals, 
            dietary_restrictions, body_fat_percentage, diabetes_type, target_calories, 
            target_carbs, target_protein, target_fat, bmr, tdee, last_active, 
            last_profile_update, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ''', (
            user_id, user_data['name'], user_data['age'], user_data['gender'],
            user_data['height'], user_data['weight'], user_data['activity_level'],
            user_data['health_goals'], user_data['dietary_restrictions'],
            user_data.get('body_fat_percentage', 0), user_data.get('diabetes_type'),
            target_calories, target_carbs, target_protein, target_fat, bmr, tdee
        ))
        conn.commit()
        conn.close()
    
    @staticmethod  
    def save_meal_record(user_id, meal_type, meal_description, analysis, nutrition_data=None):
        conn = None
        try:
            conn = sqlite3.connect('nutrition_bot.db', timeout=20.0)
            cursor = conn.cursor()
            
            print(f"🔍 DEBUG - 開始儲存記錄：{meal_type} - {meal_description}")
            print(f"🔍 DEBUG - 營養數據：{nutrition_data}")
            
            # 🔧 修正：確保營養素欄位存在
            cursor.execute("PRAGMA table_info(meal_records)")
            columns = [column[1] for column in cursor.fetchall()]
            print(f"🔍 DEBUG - meal_records 表欄位：{columns}")
            
            has_nutrition_columns = all(col in columns for col in ['calories', 'carbs', 'protein', 'fat', 'fiber', 'sugar'])
            print(f"🔍 DEBUG - 是否有營養素欄位：{has_nutrition_columns}")
            
            if not has_nutrition_columns:
                # 如果沒有營養素欄位，先添加
                nutrition_columns = [
                    ('calories', 'REAL DEFAULT 0'),
                    ('carbs', 'REAL DEFAULT 0'),
                    ('protein', 'REAL DEFAULT 0'),
                    ('fat', 'REAL DEFAULT 0'),
                    ('fiber', 'REAL DEFAULT 0'),
                    ('sugar', 'REAL DEFAULT 0')
                ]
                
                for column_name, column_type in nutrition_columns:
                    try:
                        cursor.execute(f'ALTER TABLE meal_records ADD COLUMN {column_name} {column_type}')
                        print(f"✅ 已添加營養素欄位：{column_name}")
                    except sqlite3.OperationalError as e:
                        if "duplicate column name" not in str(e):
                            print(f"❌ 添加欄位 {column_name} 失敗：{e}")
            
            # 🔧 修正：總是儲存營養數據
            if nutrition_data:
                cursor.execute('''
                    INSERT INTO meal_records 
                    (user_id, meal_type, meal_description, nutrition_analysis,
                    calories, carbs, protein, fat, fiber, sugar)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    user_id, meal_type, meal_description, analysis,
                    nutrition_data.get('calories', 0), nutrition_data.get('carbs', 0),
                    nutrition_data.get('protein', 0), nutrition_data.get('fat', 0),
                    nutrition_data.get('fiber', 0), nutrition_data.get('sugar', 0)
                ))
                print(f"✅ 已儲存完整營養數據到 meal_records")
            else:
                # 如果沒有營養數據，使用預設值
                cursor.execute('''
                    INSERT INTO meal_records 
                    (user_id, meal_type, meal_description, nutrition_analysis,
                    calories, carbs, protein, fat, fiber, sugar)
                    VALUES (?, ?, ?, ?, 0, 0, 0, 0, 0, 0)
                ''', (user_id, meal_type, meal_description, analysis))
                print(f"⚠️ 儲存記錄但無營養數據")
            
            conn.commit()
            print(f"✅ meal_records 儲存成功")
            
            # 🔧 修正：確保更新每日營養總結
            if nutrition_data:
                UserManager._update_daily_nutrition_with_conn(conn, user_id, nutrition_data)
                print(f"✅ 每日營養總結更新完成")
            
            # 更新食物偏好
            UserManager._update_food_preferences_with_conn(conn, user_id, meal_description)
            
            conn.commit()
            print(f"✅ 所有資料儲存完成")
            
        except Exception as e:
            if conn:
                conn.rollback()
            print(f"❌ 儲存記錄失敗：{e}")
            raise e
        finally:
            if conn:
                conn.close()
    
    @staticmethod
    def _update_daily_nutrition_with_conn(conn, user_id, nutrition_data):
        """使用現有連線更新每日營養總結"""
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            cursor = conn.cursor()
            
            print(f"🔍 DEBUG - 更新每日營養：{today}")
            print(f"🔍 DEBUG - 營養數據：{nutrition_data}")
            
            # 🔧 修正：確保 daily_nutrition 表存在且有正確欄位
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_nutrition (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    date TEXT,
                    total_calories REAL DEFAULT 0,
                    total_carbs REAL DEFAULT 0,
                    total_protein REAL DEFAULT 0,
                    total_fat REAL DEFAULT 0,
                    total_fiber REAL DEFAULT 0,
                    total_sugar REAL DEFAULT 0,
                    meal_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, date),
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')
            
            # 🔧 重要修正：計算今日實際餐數
            cursor.execute('''
                SELECT COUNT(*) FROM meal_records 
                WHERE user_id = ? AND DATE(recorded_at) = ?
            ''', (user_id, today))
            actual_meal_count = cursor.fetchone()[0]
            
            print(f"🔍 DEBUG - 查詢到的實際餐數：{actual_meal_count}")
            
            # 檢查今日記錄是否存在
            cursor.execute('''
                SELECT total_calories, total_carbs, total_protein, total_fat, meal_count 
                FROM daily_nutrition WHERE user_id = ? AND date = ?
            ''', (user_id, today))
            existing_record = cursor.fetchone()

            if existing_record:
                # 🔧 修正：更新現有記錄，但餐數基於實際計算
                cursor.execute('''
                    UPDATE daily_nutrition SET
                        total_calories = total_calories + ?,
                        total_carbs = total_carbs + ?,
                        total_protein = total_protein + ?,
                        total_fat = total_fat + ?,
                        total_fiber = total_fiber + ?,
                        total_sugar = total_sugar + ?,
                        meal_count = ?
                    WHERE user_id = ? AND date = ?
                ''', (
                    nutrition_data.get('calories', 0), nutrition_data.get('carbs', 0),
                    nutrition_data.get('protein', 0), nutrition_data.get('fat', 0),
                    nutrition_data.get('fiber', 0), nutrition_data.get('sugar', 0),
                    actual_meal_count,  # 🔧 使用實際計算的餐數
                    user_id, today
                ))
                print(f"✅ 更新現有每日營養記錄，餐數設為：{actual_meal_count}")
            else:
                # 🔧 修正：插入新記錄，餐數設為實際計算值
                cursor.execute('''
                    INSERT INTO daily_nutrition 
                    (user_id, date, total_calories, total_carbs, total_protein, total_fat, 
                    total_fiber, total_sugar, meal_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    user_id, today,
                    nutrition_data.get('calories', 0), nutrition_data.get('carbs', 0),
                    nutrition_data.get('protein', 0), nutrition_data.get('fat', 0),
                    nutrition_data.get('fiber', 0), nutrition_data.get('sugar', 0),
                    actual_meal_count  # 🔧 使用實際計算的餐數
                ))
                print(f"✅ 插入新的每日營養記錄，餐數設為：{actual_meal_count}")
            
            # 驗證儲存結果
            cursor.execute('''
                SELECT total_calories, total_carbs, total_protein, total_fat, meal_count 
                FROM daily_nutrition WHERE user_id = ? AND date = ?
            ''', (user_id, today))
            verification = cursor.fetchone()
            print(f"🔍 DEBUG - 儲存後驗證：{verification}")
            
        except Exception as e:
            print(f"❌ 更新每日營養總結失敗：{e}")
            raise e

    @staticmethod
    def _update_food_preferences_with_conn(conn, user_id, meal_description):
        """使用現有連線更新食物偏好記錄"""
        try:
            cursor = conn.cursor()
            
            # 擴展食物關鍵字
            food_keywords = [
                '飯', '麵', '雞肉', '豬肉', '牛肉', '魚', '蝦', '蛋', '豆腐', 
                '青菜', '高麗菜', '菠菜', '蘿蔔', '番茄', '馬鈴薯', '地瓜',
                '便當', '沙拉', '湯', '粥', '麵包', '水果', '優格', '堅果',
                '糙米', '燕麥', '雞胸肉', '鮭魚', '酪梨', '花椰菜'
            ]
            
            for keyword in food_keywords:
                if keyword in meal_description:
                    cursor.execute('''
                        SELECT frequency FROM food_preferences 
                        WHERE user_id = ? AND food_item = ?
                    ''', (user_id, keyword))
                    result = cursor.fetchone()
                    
                    if result:
                        cursor.execute('''
                            UPDATE food_preferences 
                            SET frequency = frequency + 1, last_eaten = CURRENT_TIMESTAMP
                            WHERE user_id = ? AND food_item = ?
                        ''', (user_id, keyword))
                    else:
                        cursor.execute('''
                            INSERT INTO food_preferences (user_id, food_item)
                            VALUES (?, ?)
                        ''', (user_id, keyword))
            
        except Exception as e:
            print(f"更新食物偏好失敗：{e}")


    @staticmethod
    def update_food_preferences(user_id, meal_description):
        """更新用戶食物偏好記錄"""
        conn = sqlite3.connect('nutrition_bot.db')
        cursor = conn.cursor()
        
        # 簡單的食物項目提取（可以改進為更複雜的 NLP）
        food_keywords = ['飯', '麵', '雞肉', '豬肉', '牛肉', '魚', '蝦', '蛋', '豆腐', 
                        '青菜', '高麗菜', '菠菜', '蘿蔔', '番茄', '馬鈴薯', '地瓜',
                        '便當', '沙拉', '湯', '粥', '麵包', '水果', '優格', '堅果']
        
        for keyword in food_keywords:
            if keyword in meal_description:
                # 檢查是否已存在
                cursor.execute('''
                    SELECT frequency FROM food_preferences 
                    WHERE user_id = ? AND food_item = ?
                ''', (user_id, keyword))
                result = cursor.fetchone()
                
                if result:
                    # 更新頻率
                    cursor.execute('''
                        UPDATE food_preferences 
                        SET frequency = frequency + 1, last_eaten = CURRENT_TIMESTAMP
                        WHERE user_id = ? AND food_item = ?
                    ''', (user_id, keyword))
                else:
                    # 新增記錄
                    cursor.execute('''
                        INSERT INTO food_preferences (user_id, food_item)
                        VALUES (?, ?)
                    ''', (user_id, keyword))
        
        conn.commit()
        conn.close()
    
    @staticmethod
    def update_daily_nutrition(user_id, nutrition_data):
        """更新每日營養總結"""
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            conn = sqlite3.connect('nutrition_bot.db')
            cursor = conn.cursor()
            
            # 檢查 daily_nutrition 表是否存在
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_nutrition (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    date TEXT,
                    total_calories REAL DEFAULT 0,
                    total_carbs REAL DEFAULT 0,
                    total_protein REAL DEFAULT 0,
                    total_fat REAL DEFAULT 0,
                    total_fiber REAL DEFAULT 0,
                    total_sugar REAL DEFAULT 0,
                    meal_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, date),
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')
            
            cursor.execute('''
                INSERT OR IGNORE INTO daily_nutrition (user_id, date) VALUES (?, ?)
            ''', (user_id, today))
            
            cursor.execute('''
                UPDATE daily_nutrition SET
                    total_calories = total_calories + ?,
                    total_carbs = total_carbs + ?,
                    total_protein = total_protein + ?,
                    total_fat = total_fat + ?,
                    total_fiber = total_fiber + ?,
                    total_sugar = total_sugar + ?,
                    meal_count = meal_count + 1
                WHERE user_id = ? AND date = ?
            ''', (
                nutrition_data.get('calories', 0), nutrition_data.get('carbs', 0),
                nutrition_data.get('protein', 0), nutrition_data.get('fat', 0),
                nutrition_data.get('fiber', 0), nutrition_data.get('sugar', 0),
                user_id, today
            ))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            print(f"更新每日營養總結失敗：{e}")

    @staticmethod
    def get_weekly_meals(user_id):
        conn = None
        try:
            conn = sqlite3.connect('nutrition_bot.db', timeout=10.0)
            cursor = conn.cursor()
            week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute('''
                SELECT meal_type, meal_description, nutrition_analysis, recorded_at
                FROM meal_records 
                WHERE user_id = ? AND recorded_at >= ?
                ORDER BY recorded_at DESC
            ''', (user_id, week_ago))
            records = cursor.fetchall()
            return records
        except Exception as e:
            print(f"取得週記錄錯誤：{e}")
            return []
        finally:
            if conn:
                conn.close()
    
    @staticmethod
    def get_food_preferences(user_id, limit=10):
        """取得用戶最常吃的食物"""
        conn = sqlite3.connect('nutrition_bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT food_item, frequency, last_eaten
            FROM food_preferences 
            WHERE user_id = ?
            ORDER BY frequency DESC, last_eaten DESC
            LIMIT ?
        ''', (user_id, limit))
        preferences = cursor.fetchall()
        conn.close()
        return preferences
    
    @staticmethod
    def get_recent_meals(user_id, days=3):
        """取得最近幾天的餐點"""
        conn = sqlite3.connect('nutrition_bot.db')
        cursor = conn.cursor()
        days_ago = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute('''
            SELECT meal_description, recorded_at
            FROM meal_records 
            WHERE user_id = ? AND recorded_at >= ?
            ORDER BY recorded_at DESC
            LIMIT 10
        ''', (user_id, days_ago))
        meals = cursor.fetchall()
        conn.close()
        return meals

class MessageAnalyzer:
    """分析用戶訊息意圖"""
    
    @staticmethod
    def detect_intent(message):
        message_lower = message.lower()
        
        # 飲食建議請求
        suggestion_keywords = ['推薦', '建議', '吃什麼', '不知道要吃什麼', '給我建議', 
                             '推薦食物', '今天吃什麼', '早餐吃什麼', '午餐吃什麼', '晚餐吃什麼']
        
        # 食物諮詢
        consultation_keywords = ['可以吃', '能吃', '適合', '會不會', '這個好嗎', 
                               '有什麼影響', '建議吃', '怎麼吃', '份量']
        
        # 檢查意圖
        if any(keyword in message_lower for keyword in suggestion_keywords):
            return 'suggestion'
        elif any(keyword in message_lower for keyword in consultation_keywords):
            return 'consultation'
        elif '?' in message or '？' in message:
            return 'consultation'
        else:
            return 'record'  # 預設為記錄飲食

@app.route("/", methods=['GET'])
def home():
    return "營養師機器人正在運行中！", 200

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_id = event.source.user_id
    message_text = event.message.text
    
    # 🔧 新增：處理取消請求
    if message_text.lower().strip() in ['取消', 'cancel', '不要', '算了', '沒事', '不用了']:
        handle_cancel_request(event)
        return
    
    if message_text.lower().strip() in ['重新啟動', '重啟', 'restart', 'reset', '重置', '重新開始', '清除', '初始化', '卡住了', '不動了', '重來']:
        # 清除用戶狀態
        if user_id in user_states:
            del user_states[user_id]
        
        # 重新初始化
        user_states[user_id] = {'step': 'normal'}
        
        # 提供快速選單
        quick_reply = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="設定個人資料", text="設定個人資料")),
            QuickReplyButton(action=MessageAction(label="飲食建議", text="飲食建議")),
            QuickReplyButton(action=MessageAction(label="使用說明", text="使用說明")),
            QuickReplyButton(action=MessageAction(label="我的資料", text="我的資料"))
        ])
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="""🔄 系統重新啟動成功！

✅ 所有對話狀態已清除
✅ 可以重新開始任何功能
✅ 個人資料仍然保存

🎯 現在你可以：""",
                quick_reply=quick_reply
            )
        )
        return

    # 檢查用戶狀態
    if user_id not in user_states:
        user_states[user_id] = {'step': 'normal'}

    # 🔧 新增：處理飲食記錄確認流程
    if user_states[user_id]['step'] == 'confirm_meal_record':
        handle_meal_record_confirmation(event, message_text)
        return

    # 處理個人資料設定流程
    if user_states[user_id]['step'] != 'normal':
        handle_profile_setup_flow(event, message_text)
        return
    
    # 🔧 新增：處理飲食記錄關鍵字
    if message_text.lower().strip() in ['飲食記錄', '記錄飲食', '記錄', '飲食', '記錄食物', '食物記錄']:
        handle_food_record_request(event)
        return
    
    # 主功能處理
    if message_text in ["開始", "hi", "hello", "你好", "Hello", "Hi", "Hello"]:
        handle_welcome(event)
    elif message_text == "設定個人資料":
        start_profile_setup(event)
    elif message_text == "週報告":
        generate_weekly_report(event)
    elif message_text == "我的資料":
        show_user_profile(event)
    elif message_text == "使用說明":
        show_instructions(event)
    elif message_text == "飲食建議":
        provide_meal_suggestions(event)
    elif message_text == "今日進度":
        show_daily_progress(event)
    else:
        # 分析用戶意圖
        intent = MessageAnalyzer.detect_intent(message_text)
        
        if intent == 'suggestion':
            provide_meal_suggestions(event, message_text)
        elif intent == 'consultation':
            provide_food_consultation(event, message_text)
        else:
            # 預設為記錄飲食
            analyze_food_description_with_confirmation(event, message_text)

def handle_welcome(event):
    user_id = event.source.user_id
    user = UserManager.get_user(user_id)
    
    if user:
        user_data = get_user_data(user)
        name = user_data['name'] if user_data['name'] else "朋友"
        welcome_text = f"""👋 歡迎回來，{name}！

我是你的專屬AI營養師，可以：

📝 記錄飲食：直接說「記錄飲食」或描述你吃的食物
🍽️ 推薦餐點：「今天晚餐吃什麼？」
❓ 食物諮詢：「糖尿病可以吃香蕉嗎？」
📊 健康追蹤：查看「今日進度」或「週報告」

💬 直接跟我對話就可以了！"""

        quick_reply = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="📝 記錄飲食", text="記錄飲食")),
            QuickReplyButton(action=MessageAction(label="📊 今日進度", text="今日進度")),
            QuickReplyButton(action=MessageAction(label="🍽️ 飲食建議", text="飲食建議")),
            QuickReplyButton(action=MessageAction(label="📈 週報告", text="週報告"))
        ])
    else:
        welcome_text = """👋 歡迎使用AI營養師！

我是你的專屬營養師，可以：
📝 記錄分析飲食
🍽️ 推薦適合的餐點  
❓ 回答食物相關問題
📊 提供營養報告

建議先設定個人資料，讓我給你更準確的建議！"""

        quick_reply = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="📝 設定個人資料", text="設定個人資料")),
            QuickReplyButton(action=MessageAction(label="🍽️ 飲食建議", text="飲食建議")),
            QuickReplyButton(action=MessageAction(label="📝 記錄飲食", text="記錄飲食")),
            QuickReplyButton(action=MessageAction(label="📋 使用說明", text="使用說明"))
        ])
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=welcome_text, quick_reply=quick_reply)
    )

# 🔧 新增：處理飲食記錄確認的函數
def handle_meal_record_confirmation(event, message_text):
    """處理飲食記錄確認回應（修正版）"""
    user_id = event.source.user_id
    
    if message_text == "✅ 正確，請記錄":
        # 用戶確認記錄，執行實際儲存
        confirm_data = user_states[user_id]['confirm_data']
        
        try:
            print(f"🔍 DEBUG - 開始確認儲存流程")
            print(f"🔍 DEBUG - 確認數據：{confirm_data}")

            # 🔧 新增：檢查 nutrition_data 是否存在且有效
            nutrition_data = confirm_data.get('nutrition_data', {})
            print(f"🔍 DEBUG - 營養數據詳細：{nutrition_data}")
            print(f"🔍 DEBUG - 營養數據類型：{type(nutrition_data)}")            
            
            # 🔧 新增：如果營養數據為空或無效，重新生成
            if not nutrition_data or all(v == 0 for v in nutrition_data.values()):
                print(f"⚠️ WARNING - 營養數據無效，重新生成")
                food_description = confirm_data['food_description']
                nutrition_data = get_reasonable_nutrition_data(food_description)
                print(f"🔧 DEBUG - 重新生成的營養數據：{nutrition_data}")
                
                # 更新確認數據
                confirm_data['nutrition_data'] = nutrition_data
                user_states[user_id]['confirm_data'] = confirm_data
            
            # 🔧 新增：確保營養數據格式正確
            validated_nutrition = {
                'calories': float(nutrition_data.get('calories', 0)),
                'carbs': float(nutrition_data.get('carbs', 0)),
                'protein': float(nutrition_data.get('protein', 0)),
                'fat': float(nutrition_data.get('fat', 0)),
                'fiber': float(nutrition_data.get('fiber', 0)),
                'sugar': float(nutrition_data.get('sugar', 0))
            }
            print(f"🔧 DEBUG - 驗證後營養數據：{validated_nutrition}")

            # 儲存飲食記錄
            UserManager.save_meal_record(
                user_id, 
                confirm_data['meal_type'], 
                confirm_data['food_description'], 
                confirm_data['analysis_result'], 
                validated_nutrition  # 使用驗證過的營養數據
            )
            
            # 清除確認狀態
            user_states[user_id] = {'step': 'normal'}
            
            # 🔧 新增：立即驗證儲存結果
            daily_nutrition = UserManager.get_daily_nutrition(user_id)
            print(f"🔍 DEBUG - 儲存後每日營養：{daily_nutrition}")
            
            # 發送成功確認訊息
            nutrition_data = confirm_data['nutrition_data']
            success_text = f"""✅ 飲食記錄已成功儲存！

📝 記錄內容：{confirm_data['food_description']}
🍽️ 餐型：{confirm_data['meal_type']}

📊 營養數據已加入今日統計：
• 熱量：{nutrition_data.get('calories', 0):.0f} 大卡
• 碳水：{nutrition_data.get('carbs', 0):.1f} g  
• 蛋白質：{nutrition_data.get('protein', 0):.1f} g
• 脂肪：{nutrition_data.get('fat', 0):.1f} g

💡 輸入「今日進度」可查看累計營養攝取"""
            
            quick_reply = QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="今日進度", text="今日進度")),
                QuickReplyButton(action=MessageAction(label="繼續記錄", text="繼續記錄飲食")),
                QuickReplyButton(action=MessageAction(label="飲食建議", text="飲食建議"))
            ])
            
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=success_text, quick_reply=quick_reply)
            )
            
        except Exception as e:
            # 清除確認狀態
            user_states[user_id] = {'step': 'normal'}
            
            print(f"❌ 確認儲存失敗：{e}")
            error_message = f"抱歉，儲存記錄時發生錯誤：{str(e)}\n\n請重新輸入你的飲食內容。"
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=error_message)
            )
    
    elif message_text == "❌ 錯誤，重新輸入":
        # 用戶要求重新輸入
        user_states[user_id] = {'step': 'normal'}
        
        retry_text = """🔄 好的，請重新描述你的飲食內容

💬 請詳細描述你吃的食物，例如：
• 「早餐吃了蛋餅一份加豆漿」
• 「午餐：雞腿便當，有滷蛋和青菜」
• 「晚餐：蒸魚、糙米飯、炒青菜」

🎯 提示：越詳細的描述，營養分析越準確！"""
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=retry_text)
        )
    
    else:
        # 用戶輸入了其他內容，提醒選擇
        reminder_text = """❓ 請選擇以下選項：

✅ 如果記錄資訊正確，請點選「正確，請記錄」
❌ 如果需要重新輸入，請點選「錯誤，重新輸入」"""
        
        quick_reply = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="✅ 正確，請記錄", text="✅ 正確，請記錄")),
            QuickReplyButton(action=MessageAction(label="❌ 錯誤，重新輸入", text="❌ 錯誤，重新輸入"))
        ])
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reminder_text, quick_reply=quick_reply)
        )

# 🔧 修正3：新增資料庫清理功能，清除異常的重複記錄
def clean_duplicate_nutrition_records():
    """清理 daily_nutrition 表中可能的重複記錄"""
    conn = None
    try:
        conn = sqlite3.connect('nutrition_bot.db', timeout=20.0)
        cursor = conn.cursor()
        
        print("🧹 開始清理 daily_nutrition 重複記錄...")
        
        # 查找重複記錄
        cursor.execute('''
            SELECT user_id, date, COUNT(*) as count 
            FROM daily_nutrition 
            GROUP BY user_id, date 
            HAVING COUNT(*) > 1
        ''')
        duplicates = cursor.fetchall()
        
        if duplicates:
            print(f"🔍 發現 {len(duplicates)} 組重複記錄")
            
            for user_id, date, count in duplicates:
                print(f"🔧 清理用戶 {user_id} 在 {date} 的重複記錄 ({count} 筆)")
                
                # 保留一筆記錄，刪除其他的
                cursor.execute('''
                    DELETE FROM daily_nutrition 
                    WHERE user_id = ? AND date = ? 
                    AND id NOT IN (
                        SELECT MIN(id) FROM daily_nutrition 
                        WHERE user_id = ? AND date = ?
                    )
                ''', (user_id, date, user_id, date))
                
                # 重新計算該日的正確餐數
                cursor.execute('''
                    SELECT COUNT(*) FROM meal_records 
                    WHERE user_id = ? AND DATE(recorded_at) = ?
                ''', (user_id, date))
                correct_meal_count = cursor.fetchone()[0]
                
                # 更新正確的餐數
                cursor.execute('''
                    UPDATE daily_nutrition 
                    SET meal_count = ? 
                    WHERE user_id = ? AND date = ?
                ''', (correct_meal_count, user_id, date))
                
                print(f"✅ 已修正餐數為：{correct_meal_count}")
        
        conn.commit()
        print("✅ daily_nutrition 清理完成")
        
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ 清理失敗：{e}")
    finally:
        if conn:
            conn.close()

# 🔧 修正4：新增修正所有用戶今日餐數的函數
def fix_all_users_meal_count():
    """修正所有用戶今日的餐數計算"""
    conn = None
    try:
        conn = sqlite3.connect('nutrition_bot.db', timeout=20.0)
        cursor = conn.cursor()
        today = datetime.now().strftime('%Y-%m-%d')
        
        print(f"🔧 開始修正所有用戶 {today} 的餐數...")
        
        # 取得所有今日有營養記錄的用戶
        cursor.execute('''
            SELECT DISTINCT user_id FROM daily_nutrition 
            WHERE date = ?
        ''', (today,))
        users = cursor.fetchall()
        
        for (user_id,) in users:
            # 計算該用戶今日實際餐數
            cursor.execute('''
                SELECT COUNT(*) FROM meal_records 
                WHERE user_id = ? AND DATE(recorded_at) = ?
            ''', (user_id, today))
            actual_count = cursor.fetchone()[0]
            
            # 更新正確的餐數
            cursor.execute('''
                UPDATE daily_nutrition 
                SET meal_count = ? 
                WHERE user_id = ? AND date = ?
            ''', (actual_count, user_id, today))
            
            print(f"✅ 用戶 {user_id} 餐數修正為：{actual_count}")
        
        conn.commit()
        print("✅ 所有用戶餐數修正完成")
        
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ 餐數修正失敗：{e}")
    finally:
        if conn:
            conn.close()

# 🔧 修正5：在啟動時自動執行清理和修正
def startup_database_maintenance():
    """啟動時執行資料庫維護"""
    print("🚀 執行啟動資料庫維護...")
    try:
        clean_duplicate_nutrition_records()
        fix_all_users_meal_count()
        print("✅ 資料庫維護完成")
    except Exception as e:
        print(f"❌ 資料庫維護失敗：{e}")


def show_daily_progress(event):
    """顯示今日營養進度"""
    user_id = event.source.user_id
    user = UserManager.get_user(user_id)
    
    if not user:
        # 🔧 新增：提供快速設定按鈕
        quick_reply = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="📝 設定個人資料", text="設定個人資料")),
            QuickReplyButton(action=MessageAction(label="🍽️ 先記錄飲食", text="記錄飲食")),
            QuickReplyButton(action=MessageAction(label="📋 使用說明", text="使用說明"))
        ])
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="請先設定個人資料才能查看今日進度。\n\n點選下方按鈕快速開始：",
                quick_reply=quick_reply
            )
        )
        return
    
    try:
        user_data = get_user_data(user)
        daily_nutrition = UserManager.get_daily_nutrition(user_id)
        
        # 取得今日所有餐點記錄
        today_meals = get_today_meals(user_id)
        actual_meal_count = len(today_meals) if today_meals else 0

        print(f"🔍 DEBUG - 今日實際餐數：{actual_meal_count}")
        print(f"🔍 DEBUG - daily_nutrition 中的餐數：{daily_nutrition[8] if daily_nutrition else 0}")
        
        if not daily_nutrition or actual_meal_count == 0:
            quick_reply = QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="📝 記錄早餐", text="記錄早餐")),
                QuickReplyButton(action=MessageAction(label="📝 記錄午餐", text="記錄午餐")),
                QuickReplyButton(action=MessageAction(label="📝 記錄晚餐", text="記錄晚餐")),
                QuickReplyButton(action=MessageAction(label="🍽️ 飲食建議", text="飲食建議"))
            ])
            
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    text="今天還沒有飲食記錄喔！\n\n🎯 開始記錄你的飲食，追蹤營養攝取：",
                    quick_reply=quick_reply
                )
            )
            return
        
        # 營養數據計算
        current_calories = daily_nutrition[3] or 0
        current_carbs = daily_nutrition[4] or 0
        current_protein = daily_nutrition[5] or 0
        current_fat = daily_nutrition[6] or 0
        # 🔧 使用實際計算的餐數
        meal_count = actual_meal_count
        
        # 目標數據
        target_calories = user_data['target_calories']
        target_carbs = user_data['target_carbs']
        target_protein = user_data['target_protein']
        target_fat = user_data['target_fat']
        
        # 計算進度百分比
        calories_percent = (current_calories / target_calories * 100) if target_calories > 0 else 0
        carbs_percent = (current_carbs / target_carbs * 100) if target_carbs > 0 else 0
        protein_percent = (current_protein / target_protein * 100) if target_protein > 0 else 0
        fat_percent = (current_fat / target_fat * 100) if target_fat > 0 else 0
        
        # 剩餘需求
        remaining_calories = max(0, target_calories - current_calories)
        remaining_carbs = max(0, target_carbs - current_carbs)
        remaining_protein = max(0, target_protein - current_protein)
        remaining_fat = max(0, target_fat - current_fat)
        
        # 生成進度條
        def generate_progress_bar(percent):
            if percent >= 100:
                return "🟢🟢🟢🟢🟢 100%+"
            elif percent >= 80:
                return "🟢🟢🟢🟢🟡 " + f"{percent:.0f}%"
            elif percent >= 60:
                return "🟢🟢🟢🟡🟡 " + f"{percent:.0f}%"
            elif percent >= 40:
                return "🟢🟢🟡🟡🟡 " + f"{percent:.0f}%"
            elif percent >= 20:
                return "🟢🟡🟡🟡🟡 " + f"{percent:.0f}%"
            else:
                return "🟡🟡🟡🟡🟡 " + f"{percent:.0f}%"
        
        # 組合今日進度報告
        progress_text = f"""📊 今日營養進度

👤 {user_data['name']} 的營養追蹤

🔥 熱量進度：
{generate_progress_bar(calories_percent)}
攝取：{current_calories:.0f} / 目標：{target_calories:.0f} 大卡
還需要：{remaining_calories:.0f} 大卡

🍚 碳水化合物：
{generate_progress_bar(carbs_percent)}
攝取：{current_carbs:.1f} / 目標：{target_carbs:.0f} g
還需要：{remaining_carbs:.1f} g

🥩 蛋白質：
{generate_progress_bar(protein_percent)}
攝取：{current_protein:.1f} / 目標：{target_protein:.0f} g
還需要：{remaining_protein:.1f} g

🥑 脂肪：
{generate_progress_bar(fat_percent)}
攝取：{current_fat:.1f} / 目標：{target_fat:.0f} g
還需要：{remaining_fat:.1f} g

📝 今日用餐記錄：({meal_count} 餐)
"""
        
        # 添加今日餐點列表
        if today_meals:
            for meal in today_meals:
                meal_time = meal[4][:5] if len(meal) > 4 else "未知時間"  # 取時間部分
                progress_text += f"• {meal_time} {meal[1]}：{meal[2][:30]}{'...' if len(meal[2]) > 30 else ''}\n"
        
        # 添加建議
        if calories_percent < 80:
            progress_text += f"\n💡 建議：今日熱量攝取不足，建議再攝取 {remaining_calories:.0f} 大卡"
        elif calories_percent > 120:
            over_calories = current_calories - target_calories
            progress_text += f"\n⚠️ 提醒：今日熱量已超標 {over_calories:.0f} 大卡，建議明日適量減少"
        else:
            progress_text += "\n✅ 很棒！今日營養攝取均衡"
        
        quick_reply = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="繼續記錄", text="記錄飲食")),
            QuickReplyButton(action=MessageAction(label="飲食建議", text="飲食建議")),
            QuickReplyButton(action=MessageAction(label="週報告", text="週報告"))
        ])
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=progress_text, quick_reply=quick_reply)
        )
        
    except Exception as e:
        error_message = f"抱歉，無法取得今日進度：{str(e)}"
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=error_message)
        )

def get_today_meals(user_id):
    """取得今日所有餐點記錄"""
    conn = None
    try:
        conn = sqlite3.connect('nutrition_bot.db', timeout=10.0)
        cursor = conn.cursor()
        today = datetime.now().strftime('%Y-%m-%d')
        
        print(f"🔍 DEBUG - 查詢今日餐點：user_id={user_id}, date={today}")
        
        # 🔧 修正：檢查表格結構
        cursor.execute("PRAGMA table_info(meal_records)")
        columns = [column[1] for column in cursor.fetchall()]
        print(f"🔍 DEBUG - meal_records 欄位：{columns}")
        
        cursor.execute('''
            SELECT meal_type, meal_description, nutrition_analysis, 
                   DATE(recorded_at) as meal_date, TIME(recorded_at) as meal_time,
                   calories, carbs, protein, fat
            FROM meal_records 
            WHERE user_id = ? AND DATE(recorded_at) = ?
            ORDER BY recorded_at ASC
        ''', (user_id, today))
        meals = cursor.fetchall()
        
        print(f"🔍 DEBUG - 今日餐點查詢結果：{len(meals)} 餐")
        for meal in meals:
            print(f"🔍 DEBUG - 餐點詳細：{meal}")
        
        return meals
    except Exception as e:
        print(f"❌ 取得今日餐點錯誤：{e}")
        return []
    finally:
        if conn:
            conn.close()

def provide_meal_suggestions(event, user_message=""):
    """提供飲食建議"""
    user_id = event.source.user_id
    user = UserManager.get_user(user_id)
    
    if not user:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="請先設定個人資料，我才能提供適合你的飲食建議喔！")
        )
        return
    
    try:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="🤔 讓我想想適合你的餐點...")
        )
        
        # 取得用戶最近飲食和偏好
        recent_meals = UserManager.get_recent_meals(user_id)
        food_preferences = UserManager.get_food_preferences(user_id)
        
        # 安全地取得用戶資料，避免 None 值和格式化錯誤
        # ✅ 修正後
        user_data = get_user_data(user)
        diabetes_context = f"糖尿病類型：{user_data['diabetes_type']}" if user_data['diabetes_type'] else "無糖尿病"

        user_context = f"""
用戶資料：{user_data['name']}，{user_data['age']}歲，{user_data['gender']}
身高：{user_data['height']}cm，體重：{user_data['weight']}kg，體脂率：{user_data['body_fat_percentage']:.1f}%
活動量：{user_data['activity_level']}
健康目標：{user_data['health_goals']}
飲食限制：{user_data['dietary_restrictions']}
{diabetes_context}

每日營養目標：
熱量：{user_data['target_calories']:.0f}大卡，碳水：{user_data['target_carbs']:.0f}g，蛋白質：{user_data['target_protein']:.0f}g，脂肪：{user_data['target_fat']:.0f}g

最近3天飲食：
{chr(10).join([f"- {meal[0]}" for meal in recent_meals[:5]])}

常吃食物：
{chr(10).join([f"- {pref[0]} (吃過{pref[1]}次)" for pref in food_preferences[:5]])}

用戶詢問：{user_message}
"""

        # 修改後的建議 Prompt
        suggestion_prompt = """
你是擁有20年經驗的專業營養師，特別專精糖尿病醣類控制。請根據用戶的個人資料、飲食習慣和詢問，提供個人化的餐點建議。

重要要求：
1. 每個食物都必須提供明確的份量指示
2. 使用純文字格式，不要使用任何 Markdown 符號（如 #、*、、- 等）
3. 使用表情符號和空行來區分段落

份量表達方式：
🍚 主食類：
白飯/糙米飯：1碗 = 1個拳頭大 = 約150-200g = 約200-250大卡
麵條：1份 = 約100g乾重 = 煮熟後約200g
吐司：1片全麥吐司 = 約30g = 約80大卡

🥩 蛋白質類：
雞胸肉：1份 = 1個手掌大小厚度 = 約100-120g = 約120-150大卡
魚類：1份 = 手掌大小 = 約100g = 約100-150大卡
蛋：1顆雞蛋 = 約50g = 約70大卡
豆腐：1塊 = 手掌大小 = 約100g = 約80大卡

🥬 蔬菜類：
綠葉蔬菜：1份 = 煮熟後約100g = 生菜約200g = 約25大卡
根莖類：1份 = 約100g = 約50-80大卡

🥛 其他：
堅果：1份 = 約30g = 約1湯匙 = 約180大卡
油：1茶匙 = 約5ml = 約45大卡

糖尿病患者特別注意：
優先推薦低GI食物、碳水化合物份量要精確控制、建議少量多餐、避免精製糖和高糖食物

請提供：
1. 推薦3個適合的完整餐點組合
2. 每個餐點包含：主食+蛋白質+蔬菜+適量油脂
3. 每個食物項目都要標明：具體份量（克數）+ 視覺比對（拳頭/手掌等）+ 約略熱量
4. 總熱量估算和營養素分配
5. 考慮用戶的健康目標和飲食限制
6. 避免重複最近吃過的食物
7. 提供簡單的製作方式或購買建議
8. 說明選擇這些餐點的營養理由

回應格式範例：
🍽️ 餐點1：烤雞胸肉餐

🍚 主食：糙米飯 1碗（150g）= 1拳頭大 = 約220大卡
🥩 蛋白質：烤雞胸肉 1份（120g）= 手掌大厚度 = 約150大卡  
🥬 蔬菜：炒青菜 1份（100g）= 約30大卡
🥄 油脂：橄欖油 1茶匙（5ml）= 約45大卡

總熱量：約445大卡

🍳 製作方式：雞胸肉用香料調味烤15分鐘，青菜熱炒3分鐘即可

💡 選擇理由：低脂高蛋白，適合減重目標

請用純文字格式回應，不要使用 # *  - 等符號，多用表情符號和空行讓內容清晰易讀。
"""
        
        # 使用 OpenAI 生成建議
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": suggestion_prompt},
                    {"role": "user", "content": user_context}
                ],
                max_tokens=1200,  # 增加 tokens 以容納詳細份量說明
                temperature=0.8
            )
            
            suggestions = response.choices[0].message.content
            
        except Exception as openai_error:
            suggestions = generate_detailed_meal_suggestions(user, recent_meals, food_preferences)
        
        line_bot_api.push_message(
            event.source.user_id,
            TextSendMessage(text=f"🍽️ 為你推薦的餐點：\n\n{suggestions}")
        )
        
    except Exception as e:
        error_message = f"抱歉，推薦功能出現問題：{str(e)}\n\n請稍後再試或直接詢問特定餐點建議。"
        
        line_bot_api.push_message(
            event.source.user_id,
            TextSendMessage(text=error_message)
        )

# 🔧 修正3：新增取消處理函數
def handle_cancel_request(event):
    """處理取消請求"""
    
    # 清除用戶狀態
    user_id = event.source.user_id
    if user_id in user_states:
        user_states[user_id] = {'step': 'normal'}
    
    cancel_text = """好的！👌

我一直都在，有任何問題歡迎再來詢問！

🎯 你可以隨時：
• 記錄飲食獲得營養分析
• 詢問飲食建議
• 諮詢食物相關問題
• 查看今日進度或週報告

有需要幫助的時候再叫我～ 😊"""

    quick_reply = QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="🍽️ 飲食建議", text="飲食建議")),
        QuickReplyButton(action=MessageAction(label="📝 記錄飲食", text="記錄飲食")),
        QuickReplyButton(action=MessageAction(label="📊 今日進度", text="今日進度")),
        QuickReplyButton(action=MessageAction(label="📋 使用說明", text="使用說明"))
    ])
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=cancel_text, quick_reply=quick_reply)
    )


# 🔧 修正2：新增處理飲食記錄關鍵字的函數
def handle_food_record_request(event):
    """處理飲食記錄請求，提供引導"""
    
    guide_text = """📝 飲食記錄指南

請告訴我你要記錄的內容：

📋 請包含以下資訊：
• 🕐 什麼時候：早餐/午餐/晚餐/點心
• 🍽️ 吃了什麼：具體的食物名稱
• 📏 份量多少：碗數、片數、杯數等

💬 記錄範例：
• 「早餐吃了蛋餅一份加豆漿一杯」
• 「午餐：雞腿便當，有滷蛋和高麗菜」
• 「晚餐吃了蒸魚一片、糙米飯半碗、炒青菜」
• 「下午喝了拿鐵咖啡中杯」

🎯 小提醒：
越詳細的描述，營養分析越準確！
包含份量資訊能讓我給你更精確的建議。

💬 現在請描述你吃的食物："""

    quick_reply = QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="📝 早餐記錄", text="早餐吃了")),
        QuickReplyButton(action=MessageAction(label="📝 午餐記錄", text="午餐吃了")),
        QuickReplyButton(action=MessageAction(label="📝 晚餐記錄", text="晚餐吃了")),
        QuickReplyButton(action=MessageAction(label="📝 點心記錄", text="點心吃了")),
        QuickReplyButton(action=MessageAction(label="❌ 取消", text="取消"))
    ])
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=guide_text, quick_reply=quick_reply)
    )


def provide_food_consultation(event, user_question):
    """提供食物諮詢"""
    user_id = event.source.user_id
    user = UserManager.get_user(user_id)
    
    try:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="🤔 讓我分析一下這個問題...")
        )
        
        # 準備用戶背景資訊
        if user:
            user_data = get_user_data(user)
            diabetes_context = f"糖尿病類型：{user_data['diabetes_type']}" if user_data['diabetes_type'] else "無糖尿病"
            user_context = f"""
用戶資料：{user_data['name']}，{user_data['age']}歲，{user_data['gender']}
身高：{user_data['height']}cm，體重：{user_data['weight']}kg，體脂率：{user_data['body_fat_percentage']:.1f}%
活動量：{user_data['activity_level']}
健康目標：{user_data['health_goals']}
飲食限制：{user_data['dietary_restrictions']}
{diabetes_context}
"""
        else:
            user_context = "用戶未設定個人資料，請提供一般性建議。"
        
        # 修改後的諮詢 Prompt
        consultation_prompt = f"""
你是擁有20年經驗的專業營養師，特別專精糖尿病醣類控制。請回答用戶關於食物的問題。

{user_context}

重要要求：
1. 如果涉及份量建議，必須提供明確的份量指示
2. 使用純文字格式，不要使用任何 Markdown 符號
3. 使用表情符號區分段落

份量參考：
🍚 主食: 1碗飯 = 1拳頭 = 150-200g
🥩 蛋白質: 1份肉類 = 1手掌大小厚度 = 100-120g  
🥬 蔬菜: 1份 = 煮熟後100g = 生菜200g
🥜 堅果: 1份 = 30g = 約1湯匙
🥛 飲品: 1杯 = 250ml

糖尿病患者特別考量：
重點關注血糖影響、提供GI值參考、建議適合的食用時間、給出血糖監測建議

請提供：
1. 直接回答用戶的問題（可以吃/不建議/適量等）
2. 說明原因（營養成分、健康影響）  
3. 如果可以吃，明確建議份量：
   具體重量（克數）
   視覺比對（拳頭/手掌/湯匙等）
   建議頻率（每天/每週幾次）
   最佳食用時間
4. 如果不建議，提供份量明確的替代選項
5. 針對用戶健康狀況的特別提醒

回應格式範例：
💡 關於香蕉的建議

✅ 可以適量食用

🍌 建議份量：半根中型香蕉（約60g）= 約60大卡

⏰ 最佳時機：運動後30分鐘或兩餐之間

🩺 糖尿病注意：香蕉GI值中等，建議搭配堅果一起吃可緩解血糖上升

請用純文字格式，多用表情符號，不要使用 # *  等符號。
"""
        
        # 使用 OpenAI 分析
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": consultation_prompt},
                    {"role": "user", "content": f"用戶問題：{user_question}"}
                ],
                max_tokens=800,
                temperature=0.7
            )
            
            consultation_result = response.choices[0].message.content
            
        except Exception as openai_error:
            consultation_result = generate_detailed_food_consultation(user_question, user)
        
        line_bot_api.push_message(
            event.source.user_id,
            TextSendMessage(text=f"💡 營養師建議：\n\n{consultation_result}")
        )
        
    except Exception as e:
        error_message = f"抱歉，諮詢功能出現問題：{str(e)}\n\n請重新描述你的問題，我會盡力回答。"
        
        line_bot_api.push_message(
            event.source.user_id,
            TextSendMessage(text=error_message)
        )

def generate_basic_meal_suggestions(user, recent_meals, food_preferences):
    """API 不可用時的基本餐點建議"""
    
    health_goal = user[7] if user[7] else "維持健康"
    restrictions = user[8] if user[8] else "無"
    
    suggestions = f"""根據你的健康目標「{health_goal}」，推薦以下餐點：

🥗 均衡餐點建議：
• 糙米飯 + 蒸魚 + 炒青菜
• 雞胸肉沙拉 + 全麥麵包
• 豆腐味噌湯 + 烤蔬菜

🍎 健康點心：
• 堅果優格
• 水果拼盤
• 無糖豆漿

💡 注意事項：
• 飲食限制：{restrictions}
• 建議少油少鹽
• 多攝取蔬果和蛋白質

詳細營養分析功能暫時無法使用，以上為一般性建議。"""
    
    return suggestions

def generate_basic_food_consultation(question, user):
    """API 不可用時的基本食物諮詢"""
    
    consultation = f"""關於你的問題「{question}」：

💡 一般建議：
• 任何食物都要適量攝取
• 注意個人健康狀況
• 均衡飲食最重要

📋 建議做法：
• 如有特殊疾病，請諮詢醫師
• 注意份量控制
• 選擇天然原型食物

⚠️ 特別提醒：
詳細營養諮詢功能暫時無法使用，建議諮詢專業營養師或醫師獲得個人化建議。"""
    
    return consultation

# 其他原有功能保持不變...
def start_profile_setup(event):
    user_id = event.source.user_id
    user_states[user_id] = {'step': 'name', 'data': {}}
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="📝 讓我為你建立個人營養檔案！\n\n請告訴我你的姓名：")
    )

def handle_profile_setup_flow(event, message_text):
    user_id = event.source.user_id
    current_step = user_states[user_id]['step']
    
    if current_step == 'name':
        user_states[user_id]['data']['name'] = message_text
        user_states[user_id]['step'] = 'age'
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"很高興認識你，{message_text}！\n\n請告訴我你的年齡：")
        )
    
    elif current_step == 'age':
        try:
            age = int(re.findall(r'\d+', message_text)[0])  # 提取數字
            if 10 <= age <= 120:  # 合理年齡範圍
                user_states[user_id]['data']['age'] = age
                user_states[user_id]['step'] = 'gender'
                
                quick_reply = QuickReply(items=[
                    QuickReplyButton(action=MessageAction(label="男性", text="男性")),
                    QuickReplyButton(action=MessageAction(label="女性", text="女性"))
                ])
                
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="請選擇你的性別：", quick_reply=quick_reply)
                )
            else:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="年齡請輸入10-120之間的數字：")
                )
        except (ValueError, IndexError):
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="請輸入有效的年齡數字（例如：25）：")
            )
    
    elif current_step == 'gender':
        # 智能識別性別輸入
        message_lower = message_text.lower().strip()
        
        if message_lower in ['男性', '男', 'male', 'm', '1', '先生']:
            gender = '男性'
        elif message_lower in ['女性', '女', 'female', 'f', '2', '小姐']:
            gender = '女性'
        else:
            # 無法識別時，重新詢問
            quick_reply = QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="男性", text="男性")),
                QuickReplyButton(action=MessageAction(label="女性", text="女性"))
            ])
            
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="請選擇你的性別（請點選下方按鈕或輸入「男性」、「女性」）：", quick_reply=quick_reply)
            )
            return
        
        user_states[user_id]['data']['gender'] = gender
        user_states[user_id]['step'] = 'height'
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="請告訴我你的身高（公分）：")
        )        

    elif current_step == 'height':
        try:
            height = float(message_text)
            user_states[user_id]['data']['height'] = height
            user_states[user_id]['step'] = 'weight'
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="請告訴我你的體重（公斤）：")
            )
        except ValueError:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="請輸入有效的身高數字：")
            )
        
    elif current_step == 'weight':
        try:
            weight = float(message_text)
            user_states[user_id]['data']['weight'] = weight
            user_states[user_id]['step'] = 'body_fat'
            
            # 估算體脂率
            data = user_states[user_id]['data']
            height_m = data['height'] / 100
            bmi = weight / (height_m ** 2)
            
            # 簡單的體脂率估算
            if data['gender'] == '男性':
                estimated_body_fat = (1.20 * bmi) + (0.23 * data['age']) - 16.2
            else:
                estimated_body_fat = (1.20 * bmi) + (0.23 * data['age']) - 5.4
            
            estimated_body_fat = max(5, min(50, estimated_body_fat))
            
            quick_reply = QuickReply(items=[
                QuickReplyButton(action=MessageAction(label=f"使用估算值 {estimated_body_fat:.1f}%", text=f"估算 {estimated_body_fat:.1f}")),
                QuickReplyButton(action=MessageAction(label="輸入實測值", text="實測值")),
                QuickReplyButton(action=MessageAction(label="跳過此項", text="跳過體脂"))
            ])
            
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"📊 體脂率設定\n\n根據你的BMI，估算體脂率約為 {estimated_body_fat:.1f}%\n\n請選擇：", quick_reply=quick_reply)
            )
        except ValueError:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="請輸入有效的體重數字：")
            )

    elif current_step == 'body_fat':
        if "估算" in message_text:
            # 使用估算值
            data = user_states[user_id]['data']
            height_m = data['height'] / 100
            bmi = data['weight'] / (height_m ** 2)
            
            if data['gender'] == '男性':
                body_fat = (1.20 * bmi) + (0.23 * data['age']) - 16.2
            else:
                body_fat = (1.20 * bmi) + (0.23 * data['age']) - 5.4
            
            body_fat = max(5, min(50, body_fat))
            user_states[user_id]['data']['body_fat_percentage'] = body_fat
            user_states[user_id]['step'] = 'activity'
            
            quick_reply = QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="低活動量", text="低活動量")),
                QuickReplyButton(action=MessageAction(label="中等活動量", text="中等活動量")),
                QuickReplyButton(action=MessageAction(label="高活動量", text="高活動量"))
            ])
            
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="請選擇你的活動量：", quick_reply=quick_reply)
            )
        elif "實測值" in message_text:
            user_states[user_id]['step'] = 'body_fat_input'
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="請輸入你實際測量的體脂率（%）：")
            )
        elif "跳過" in message_text:
            user_states[user_id]['data']['body_fat_percentage'] = 0
            user_states[user_id]['step'] = 'activity'
            
            quick_reply = QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="低活動量", text="低活動量")),
                QuickReplyButton(action=MessageAction(label="中等活動量", text="中等活動量")),
                QuickReplyButton(action=MessageAction(label="高活動量", text="高活動量"))
            ])
            
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="請選擇你的活動量：", quick_reply=quick_reply)
            )

    elif current_step == 'body_fat_input':
        try:
            body_fat = float(message_text)
            if 5 <= body_fat <= 50:
                user_states[user_id]['data']['body_fat_percentage'] = body_fat
                user_states[user_id]['step'] = 'activity'
                
                quick_reply = QuickReply(items=[
                    QuickReplyButton(action=MessageAction(label="低活動量", text="低活動量")),
                    QuickReplyButton(action=MessageAction(label="中等活動量", text="中等活動量")),
                    QuickReplyButton(action=MessageAction(label="高活動量", text="高活動量"))
                ])
                
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="請選擇你的活動量：", quick_reply=quick_reply)
                )
            else:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="體脂率應在5-50%之間，請重新輸入：")
                )
        except ValueError:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="請輸入有效的體脂率數字：")
            )
    
    elif current_step == 'activity':
        # 智能識別活動量輸入
        message_lower = message_text.lower().strip()
        
        if message_lower in ['低活動量', '低', 'low', '1', '很少運動', '久坐']:
            activity = '低活動量'
        elif message_lower in ['中等活動量', '中等', '中', 'medium', '2', '適度運動']:
            activity = '中等活動量'
        elif message_lower in ['高活動量', '高', 'high', '3', '經常運動', '很多運動']:
            activity = '高活動量'
        else:
            # 無法識別時，重新詢問
            quick_reply = QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="低活動量", text="低活動量")),
                QuickReplyButton(action=MessageAction(label="中等活動量", text="中等活動量")),
                QuickReplyButton(action=MessageAction(label="高活動量", text="高活動量"))
            ])
            
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="請選擇你的活動量：\n\n低活動量(1)：很少運動\n中等活動量(2)：每週運動2-3次\n高活動量(3)：每天都運動\n\n請點選按鈕或輸入數字1-3：", quick_reply=quick_reply)
            )
            return
        
        user_states[user_id]['data']['activity_level'] = activity
        user_states[user_id]['step'] = 'health_goals'
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="請描述你的健康目標（例如：減重、增肌、控制血糖、維持健康）：")
        )
    
    elif current_step == 'health_goals':
        user_states[user_id]['data']['health_goals'] = message_text
        user_states[user_id]['step'] = 'dietary_restrictions'
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="最後，請告訴我你的飲食限制或過敏（例如：素食、糖尿病、高血壓、堅果過敏，沒有請輸入「無」）：")
        )
    
    elif current_step == 'dietary_restrictions':
        user_states[user_id]['data']['dietary_restrictions'] = message_text
        
        # 儲存用戶資料
        UserManager.save_user(user_id, user_states[user_id]['data'])
        user_states[user_id]['step'] = 'normal'
        
        # 計算 BMI
        data = user_states[user_id]['data']
        bmi = data['weight'] / ((data['height'] / 100) ** 2)
        
        completion_text = f"""✅ 個人資料設定完成！

📊 你的基本資訊：
• 姓名：{data['name']}
• 年齡：{data['age']} 歲
• 性別：{data['gender']}
• 身高：{data['height']} cm
• 體重：{data['weight']} kg
• BMI：{bmi:.1f}
• 活動量：{data['activity_level']}
• 健康目標：{data['health_goals']}
• 飲食限制：{data['dietary_restrictions']}

現在可以：
📝 記錄飲食獲得分析
🍽️ 詢問餐點建議
❓ 諮詢食物問題"""
        
        quick_reply = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="飲食建議", text="等等可以吃什麼？")),
            QuickReplyButton(action=MessageAction(label="食物諮詢", text="我可以吃巧克力嗎？")),
            QuickReplyButton(action=MessageAction(label="使用說明", text="使用說明"))
        ])
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=completion_text, quick_reply=quick_reply)
        )

def show_user_profile(event):
    user_id = event.source.user_id
    user = UserManager.get_user(user_id)
    
    if not user:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="你還沒有設定個人資料。請先點選「設定個人資料」。")
        )
        return
    
    user_data = get_user_data(user)
    bmi = user_data['weight'] / ((user_data['height'] / 100) ** 2)

    profile_text = f"""👤 你的個人資料：

- 姓名：{user_data['name']}
- 年齡：{user_data['age']} 歲  
- 性別：{user_data['gender']}
- 身高：{user_data['height']} cm
- 體重：{user_data['weight']} kg
- 體脂率：{user_data['body_fat_percentage']:.1f}%
- BMI：{bmi:.1f}
- 活動量：{user_data['activity_level']}
- 健康目標：{user_data['health_goals']}
- 飲食限制：{user_data['dietary_restrictions']}

💡 想要更新資料，請點選「設定個人資料」重新設定。"""
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=profile_text)
    )

def analyze_food_description_with_confirmation(event, food_description):
    """帶確認流程的飲食分析（修正營養提取版）"""
    user_id = event.source.user_id
    user = UserManager.get_user(user_id)
    
    print(f"🔍 DEBUG - 用戶輸入：{food_description}")
    
    try:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="🔍 正在分析你的飲食內容，請稍候...")
        )
        
        # 判斷餐型
        meal_type = determine_meal_type(food_description)
        print(f"🔍 DEBUG - 判斷餐型：{meal_type}")
        
        # 建立個人化提示
        if user:
            user_data = get_user_data(user)
            user_context = f"""
用戶資料：
- 姓名：{user_data['name']}，{user_data['age']}歲，{user_data['gender']}
- 身高：{user_data['height']}cm，體重：{user_data['weight']}kg，體脂率：{user_data['body_fat_percentage']:.1f}%
- 活動量：{user_data['activity_level']}
- 健康目標：{user_data['health_goals']}
- 飲食限制：{user_data['dietary_restrictions']}
- 糖尿病類型：{user_data['diabetes_type'] if user_data['diabetes_type'] else '無'}

每日營養目標：
熱量：{user_data['target_calories']:.0f}大卡，碳水：{user_data['target_carbs']:.0f}g，蛋白質：{user_data['target_protein']:.0f}g，脂肪：{user_data['target_fat']:.0f}g
"""
        else:
            user_context = "用戶未設定個人資料，請提供一般性建議。"
        
        # 使用營養分析 Prompt
        nutrition_prompt = get_updated_nutrition_prompt(user_context)
        
        # 初始化營養數據變數
        nutrition_data = None
        analysis_result = ""

        # 使用 OpenAI 分析
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": nutrition_prompt},
                    {"role": "user", "content": f"請分析以下{meal_type}：{food_description}"}
                ],
                max_tokens=1000,
                temperature=0.7
            )
            
            analysis_result = response.choices[0].message.content
            print(f"🔍 DEBUG - AI分析結果：{analysis_result}")
            
            # 🔧 重要修正：從完整的分析結果中提取營養數據
            # 不只從AI分析結果提取，也從食物描述中推測
            nutrition_data = extract_nutrition_from_analysis_with_validation(analysis_result, food_description)
            print(f"🔍 DEBUG - 第一次提取的營養數據：{nutrition_data}")
            
            # 🔧 新增：如果提取的營養數據都是0或過小，直接從分析文本中強制提取
            if not nutrition_data or all(v <= 0 for v in nutrition_data.values()):
                print(f"⚠️ WARNING - 第一次提取失敗，嘗試強制提取")
                nutrition_data = force_extract_nutrition_from_text(analysis_result)
                print(f"🔧 DEBUG - 強制提取的營養數據：{nutrition_data}")
            
            # 🔧 新增：如果還是沒有合理數據，使用描述推測
            if not nutrition_data or nutrition_data.get('calories', 0) < 50:
                print(f"⚠️ WARNING - 強制提取也失敗，使用食物描述推測")
                nutrition_data = smart_estimate_nutrition_from_description(food_description)
                print(f"🔧 DEBUG - 智能推測的營養數據：{nutrition_data}")
            
        except Exception as openai_error:
            print(f"🔍 DEBUG - OpenAI錯誤：{openai_error}")
            
            # API失敗時使用智能推測
            nutrition_data = smart_estimate_nutrition_from_description(food_description)
            analysis_result = f"系統分析：{food_description}\n\n基於食物資料庫估算營養成分"
        
        # 🔧 最終驗證營養數據
        if not nutrition_data or not isinstance(nutrition_data, dict):
            print(f"❌ ERROR - 營養數據最終檢查失敗，使用緊急備用")
            nutrition_data = emergency_nutrition_fallback(food_description)
        
        # 確保所有必需的營養素存在且不為0
        required_nutrients = ['calories', 'carbs', 'protein', 'fat', 'fiber', 'sugar']
        for nutrient in required_nutrients:
            if nutrient not in nutrition_data or nutrition_data[nutrient] <= 0:
                # 根據食物描述給予最小合理值
                default_values = {
                    'calories': 200, 'carbs': 25, 'protein': 15, 
                    'fat': 8, 'fiber': 3, 'sugar': 5
                }
                nutrition_data[nutrient] = default_values[nutrient]
                print(f"🔧 DEBUG - {nutrient} 設為預設值：{default_values[nutrient]}")
        
        print(f"🔧 DEBUG - 最終確認的營養數據：{nutrition_data}")
        
        # 顯示確認訊息
        show_meal_record_confirmation(event, user_id, meal_type, food_description, analysis_result, nutrition_data)
        
    except Exception as e:
        print(f"🔍 DEBUG - 系統錯誤：{e}")
        error_message = f"抱歉，分析出現問題：{str(e)}\n\n請重新描述你的飲食內容。"
        
        line_bot_api.push_message(
            event.source.user_id,
            TextSendMessage(text=error_message)
        )

# 🔧 新增：強制從文本中提取營養數據的函數
def force_extract_nutrition_from_text(text):
    """強制從分析文本中提取營養數據，使用更靈活的模式"""
    import re
    
    print(f"🔍 DEBUG - 強制提取營養數據：{text}")
    
    # 更寬鬆的正則表達式模式
    patterns = {
        'calories': [
            r'熱量[:：]?\s*約?(\d+(?:\.\d+)?)\s*大卡',
            r'約\s*(\d+(?:\.\d+)?)\s*大卡',
            r'(\d+)\s*大卡',
            r'總共\s*(\d+)\s*大卡'
        ],
        'carbs': [
            r'碳水化合物[:：]?\s*約?(\d+(?:\.\d+)?)\s*g',
            r'碳水[:：]?\s*約?(\d+(?:\.\d+)?)\s*g',
            r'碳水\s*(\d+)\s*g'
        ],
        'protein': [
            r'蛋白質[:：]?\s*約?(\d+(?:\.\d+)?)\s*g',
            r'蛋白質\s*(\d+)\s*g'
        ],
        'fat': [
            r'脂肪[:：]?\s*約?(\d+(?:\.\d+)?)\s*g',
            r'脂肪\s*(\d+)\s*g'
        ],
        'fiber': [
            r'纖維[:：]?\s*約?(\d+(?:\.\d+)?)\s*g',
            r'膳食纖維[:：]?\s*約?(\d+(?:\.\d+)?)\s*g',
            r'纖維\s*(\d+)\s*g'
        ]
    }
    
    def force_extract_value(patterns_list, text):
        for pattern in patterns_list:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                try:
                    value = float(matches[0])
                    print(f"🔧 DEBUG - 強制提取成功 {pattern}: {value}")
                    return value
                except (ValueError, IndexError):
                    continue
        return 0
    
    # 強制提取各營養素
    nutrition_data = {}
    for nutrient, pattern_list in patterns.items():
        value = force_extract_value(pattern_list, text)
        nutrition_data[nutrient] = value
        print(f"🔧 DEBUG - {nutrient} 強制提取結果: {value}")
    
    # 新增糖分預設值
    nutrition_data['sugar'] = nutrition_data.get('sugar', 5)
    
    print(f"🔧 DEBUG - 強制提取完成：{nutrition_data}")
    return nutrition_data

# 🔧 新增：基於食物描述的智能營養推測
def smart_estimate_nutrition_from_description(food_description):
    """根據食物描述智能推測營養數據"""
    
    print(f"🔍 DEBUG - 智能推測食物：{food_description}")
    
    food_lower = food_description.lower()
    
    # 更詳細的食物營養數據庫
    food_nutrition_db = {
        # 肉類和蛋白質
        '牛肉': {'calories': 250, 'carbs': 0, 'protein': 26, 'fat': 17, 'fiber': 0, 'sugar': 0},
        '漢堡排': {'calories': 280, 'carbs': 5, 'protein': 20, 'fat': 20, 'fiber': 1, 'sugar': 2},
        '雞蛋': {'calories': 70, 'carbs': 1, 'protein': 6, 'fat': 5, 'fiber': 0, 'sugar': 1},
        '煎蛋': {'calories': 90, 'carbs': 1, 'protein': 6, 'fat': 7, 'fiber': 0, 'sugar': 1},
        
        # 主食類
        '麵包': {'calories': 80, 'carbs': 15, 'protein': 3, 'fat': 1, 'fiber': 2, 'sugar': 2},
        '白飯': {'calories': 280, 'carbs': 62, 'protein': 6, 'fat': 1, 'fiber': 1, 'sugar': 0},
        
        # 蔬菜類
        '花椰菜': {'calories': 25, 'carbs': 5, 'protein': 3, 'fat': 0, 'fiber': 3, 'sugar': 2},
        '青菜': {'calories': 20, 'carbs': 4, 'protein': 2, 'fat': 0, 'fiber': 2, 'sugar': 2},
        
        # 乳製品
        '起司': {'calories': 100, 'carbs': 1, 'protein': 7, 'fat': 8, 'fiber': 0, 'sugar': 1},
        
        # 複合食物
        '便當': {'calories': 650, 'carbs': 80, 'protein': 25, 'fat': 20, 'fiber': 5, 'sugar': 8},
        '漢堡': {'calories': 540, 'carbs': 45, 'protein': 25, 'fat': 31, 'fiber': 3, 'sugar': 5}
    }
    
    # 分析食物描述中的關鍵字
    total_nutrition = {'calories': 0, 'carbs': 0, 'protein': 0, 'fat': 0, 'fiber': 0, 'sugar': 0}
    
    # 尋找匹配的食物
    matches_found = []
    for food_keyword, nutrition in food_nutrition_db.items():
        if food_keyword in food_lower:
            matches_found.append((food_keyword, nutrition))
            for nutrient in total_nutrition:
                total_nutrition[nutrient] += nutrition[nutrient]
            print(f"🔧 DEBUG - 匹配到食物：{food_keyword} = {nutrition}")
    
    # 如果沒有匹配到任何食物，使用描述長度和複雜度推測
    if not matches_found:
        complexity_score = len(food_description.split()) + food_description.count('，') + food_description.count('、')
        base_calories = min(150 + complexity_score * 50, 800)  # 基於描述複雜度
        
        total_nutrition = {
            'calories': base_calories,
            'carbs': base_calories * 0.4 / 4,  # 40% 來自碳水
            'protein': base_calories * 0.25 / 4,  # 25% 來自蛋白質
            'fat': base_calories * 0.35 / 9,  # 35% 來自脂肪
            'fiber': max(2, complexity_score),
            'sugar': max(3, complexity_score * 0.5)
        }
        print(f"🔧 DEBUG - 未匹配，使用複雜度推測：{total_nutrition}")
    
    # 確保數值合理
    for nutrient in total_nutrition:
        total_nutrition[nutrient] = max(0, round(total_nutrition[nutrient], 1))
    
    print(f"🔧 DEBUG - 智能推測最終結果：{total_nutrition}")
    return total_nutrition

# 🔧 新增：緊急備用營養數據
def emergency_nutrition_fallback(food_description):
    """緊急情況下的營養數據備用方案"""
    
    print(f"🚨 DEBUG - 緊急備用方案：{food_description}")
    
    # 根據描述長度和內容給予合理的營養估計
    desc_length = len(food_description)
    word_count = len(food_description.split())
    
    # 基礎熱量計算
    if desc_length < 10:  # 簡單描述
        base_calories = 150
    elif desc_length < 30:  # 中等描述
        base_calories = 300
    else:  # 詳細描述
        base_calories = 500
    
    # 根據關鍵字調整
    if any(word in food_description.lower() for word in ['便當', '漢堡', '炸', '披薩']):
        base_calories += 200
    if any(word in food_description.lower() for word in ['沙拉', '蔬菜', '水果']):
        base_calories -= 100
    
    base_calories = max(100, min(800, base_calories))  # 限制在合理範圍
    
    fallback_nutrition = {
        'calories': base_calories,
        'carbs': round(base_calories * 0.45 / 4, 1),  # 45% 碳水
        'protein': round(base_calories * 0.2 / 4, 1),  # 20% 蛋白質
        'fat': round(base_calories * 0.35 / 9, 1),  # 35% 脂肪
        'fiber': max(2, word_count // 2),
        'sugar': max(3, word_count // 3)
    }
    
    print(f"🚨 DEBUG - 緊急備用數據：{fallback_nutrition}")
    return fallback_nutrition

# 🔧 修正2：更新營養分析 Prompt，加入份量預設邏輯
def get_updated_nutrition_prompt(user_context):
    """取得更新的營養分析提示，包含份量預設邏輯"""
    
    return f"""
你是一位擁有20年經驗的專業營養師，特別專精糖尿病醣類控制。請根據用戶實際吃的食物進行分析。

{user_context}

重要原則：
1. 只分析用戶實際描述的食物，不要添加或建議其他餐點
2. 對於常見食物要使用準確的營養數據
3. 🔧 新增：份量預設規則
   - 飲料類（豆漿、咖啡、奶茶、果汁等）：沒特別註明時以 330ml 計算
   - 一般食物：沒特別註明時以 1份 計算
   - 如果用戶有明確說明份量，則以用戶描述為準
4. 使用純文字格式，多用表情符號

🔍 常見食物營養參考（請嚴格依照）：

🥛 飲料類（330ml基準）：
• 豆漿：熱量132大卡，碳水13g，蛋白質9g，脂肪5g
• 咖啡（黑咖啡）：熱量7大卡，碳水1g，蛋白質0g，脂肪0g
• 拿鐵：熱量198大卡，碳水16g，蛋白質11g，脂肪11g
• 奶茶：熱量231大卡，碳水35g，蛋白質7g，脂肪8g
• 果汁：熱量145大卡，碳水36g，蛋白質1g，脂肪0g

🍚 主食類（1份基準）：
• 白飯1碗(150g)：熱量280大卡，碳水62g，蛋白質6g，脂肪1g
• 糙米飯1碗(150g)：熱量220大卡，碳水45g，蛋白質5g，脂肪2g
• 全麥吐司1片(30g)：熱量80大卡，碳水15g，蛋白質3g，脂肪1g

🥩 蛋白質類（1份基準）：
• 雞蛋1顆(50g)：熱量70大卡，碳水1g，蛋白質6g，脂肪5g
• 雞胸肉1份(100g)：熱量165大卡，碳水0g，蛋白質31g，脂肪4g
• 魚類1份(100g)：熱量140大卡，碳水0g，蛋白質26g，脂肪3g

🥬 蔬菜類（1份基準）：
• 青菜1份(100g)：熱量25大卡，碳水5g，蛋白質3g，脂肪0g
• 沙拉1份(150g)：熱量50大卡，碳水8g，蛋白質3g，脂肪1g

🍎 水果類（1份基準）：
• 香蕉1根(100g)：熱量90大卡，碳水23g，蛋白質1g，脂肪0g
• 蘋果1個(150g)：熱量80大卡，碳水21g，蛋白質0g，脂肪0g

份量判斷規則：
- 如果用戶說「豆漿」沒特別說明 → 預設330ml
- 如果用戶說「雞蛋」沒特別說明 → 預設1顆
- 如果用戶說「飯」沒特別說明 → 預設1碗
- 如果用戶明確說「豆漿1杯」→ 以250ml計算
- 如果用戶明確說「雞蛋2顆」→ 以2顆計算

請提供：

🔍 實際攝取分析：
只分析用戶描述的這一餐，包括：
熱量：約XX大卡
碳水化合物：XXg
蛋白質：XXg
脂肪：XXg
纖維：XXg

💡 份量說明：
明確標示使用的份量（例如：豆漿330ml、雞蛋1顆）

💡 這一餐評價：
基於用戶健康目標評估這餐是否合適
這餐的優點和可改進之處

🍽️ 下次進食建議：
適合的食物類型和份量建議

特別注意：
不要建議用戶"今天還需要吃什麼來補足營養"
不要假設一天必須吃三餐
只針對實際吃的食物給建議
尊重用戶的飲食節奏
嚴格按照份量預設規則提供數據
在分析中明確說明使用的份量假設
確保營養數據的合理性
"""

# 🔧 新增：顯示記錄確認的函數
def show_meal_record_confirmation(event, user_id, meal_type, food_description, analysis_result, nutrition_data):
    """顯示飲食記錄確認訊息（確保營養數據正確版）"""
    
    print(f"🔍 DEBUG - show_meal_record_confirmation 收到的數據：")
    print(f"   meal_type: {meal_type}")
    print(f"   food_description: {food_description}")
    print(f"   nutrition_data: {nutrition_data}")
    print(f"   nutrition_data type: {type(nutrition_data)}")

    # 🔧 最終檢查：確保營養數據有效且不為0
    if not nutrition_data or not isinstance(nutrition_data, dict) or all(v <= 0 for v in nutrition_data.values()):
        print(f"⚠️ WARNING - 顯示階段營養數據無效，重新生成")
        nutrition_data = smart_estimate_nutrition_from_description(food_description)
        print(f"🔧 DEBUG - 顯示階段重新生成營養數據：{nutrition_data}")
    
    # 確保所有營養素都有合理數值
    required_nutrients = ['calories', 'carbs', 'protein', 'fat', 'fiber', 'sugar']
    min_values = {'calories': 50, 'carbs': 5, 'protein': 3, 'fat': 2, 'fiber': 1, 'sugar': 1}
    
    for nutrient in required_nutrients:
        if nutrient not in nutrition_data or nutrition_data[nutrient] <= 0:
            nutrition_data[nutrient] = min_values[nutrient]
            print(f"⚠️ WARNING - {nutrient} 設為最小值：{min_values[nutrient]}")
    
    # 轉換為正確的數值類型
    try:
        for key in nutrition_data:
            nutrition_data[key] = float(nutrition_data[key])
    except (ValueError, TypeError) as e:
        print(f"❌ ERROR - 營養數據轉換失敗：{e}")
        nutrition_data = emergency_nutrition_fallback(food_description)
    
    print(f"🔧 DEBUG - 顯示階段最終營養數據：{nutrition_data}")

    # 將確認資料暫存到用戶狀態
    user_states[user_id] = {
        'step': 'confirm_meal_record',
        'confirm_data': {
            'meal_type': meal_type,
            'food_description': food_description,
            'analysis_result': analysis_result,
            'nutrition_data': nutrition_data  # 確保這裡有正確的數據
        }
    }
    
    print(f"🔍 DEBUG - 儲存到 user_states 的數據：{user_states[user_id]['confirm_data']['nutrition_data']}")
    
    # 組合確認顯示訊息
    confirmation_display = f"""📋 請確認飲食記錄資訊

🍽️ 餐型：{meal_type}
📝 記錄內容：{food_description}

📊 營養分析：
熱量：{nutrition_data.get('calories', 0):.0f} 大卡
碳水化合物：{nutrition_data.get('carbs', 0):.1f} g
蛋白質：{nutrition_data.get('protein', 0):.1f} g
脂肪：{nutrition_data.get('fat', 0):.1f} g
纖維：{nutrition_data.get('fiber', 0):.1f} g

{analysis_result}

❓ 以上資訊是否正確？"""
    
    # 提供確認選項
    quick_reply = QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="✅ 正確，請記錄", text="✅ 正確，請記錄")),
        QuickReplyButton(action=MessageAction(label="❌ 錯誤，重新輸入", text="❌ 錯誤，重新輸入"))
    ])
    
    line_bot_api.push_message(
        event.source.user_id,
        TextSendMessage(text=confirmation_display, quick_reply=quick_reply)
    )

def extract_nutrition_from_analysis(analysis_text):
    """從分析文本中提取營養數據（保留份量校正的加強版）"""
    import re
    
    print(f"🔍 DEBUG - 開始提取營養數據從：{analysis_text[:200]}...")
    
    # 更全面的正則表達式提取
    patterns = {
        'calories': [
            r'熱量[:：]\s*約?(\d+(?:\.\d+)?)\s*大卡',
            r'總熱量[:：]\s*約?(\d+(?:\.\d+)?)\s*大卡',
            r'約(\d+(?:\.\d+)?)\s*大卡',
            r'(\d+(?:\.\d+)?)\s*大卡'
        ],
        'carbs': [
            r'碳水化合物[:：]\s*約?(\d+(?:\.\d+)?)\s*g',
            r'碳水[:：]\s*約?(\d+(?:\.\d+)?)\s*g'
        ],
        'protein': [
            r'蛋白質[:：]\s*約?(\d+(?:\.\d+)?)\s*g'
        ],
        'fat': [
            r'脂肪[:：]\s*約?(\d+(?:\.\d+)?)\s*g'
        ],
        'fiber': [
            r'纖維[:：]\s*約?(\d+(?:\.\d+)?)\s*g',
            r'膳食纖維[:：]\s*約?(\d+(?:\.\d+)?)\s*g'
        ],
        'sugar': [
            r'糖[:：]\s*約?(\d+(?:\.\d+)?)\s*g',
            r'糖分[:：]\s*約?(\d+(?:\.\d+)?)\s*g'
        ]
    }
    
    def extract_value(patterns_list, text, default=0):
        for pattern in patterns_list:
            matches = re.findall(pattern, text)
            if matches:
                try:
                    # 取第一個匹配的數值
                    value = float(matches[0])
                    print(f"🔍 DEBUG - 提取到 {pattern}: {value}")
                    return value
                except (ValueError, IndexError):
                    continue
        print(f"⚠️ WARNING - 未能提取數值，使用預設值：{default}")
        return default
    
    # 提取各營養素
    nutrition_data = {}
    for nutrient, pattern_list in patterns.items():
        nutrition_data[nutrient] = extract_value(pattern_list, analysis_text, 
                                                {'calories': 150, 'carbs': 20, 'protein': 8, 
                                                 'fat': 5, 'fiber': 2, 'sugar': 5}[nutrient])
    
    print(f"🔧 DEBUG - 原始提取的營養數據：{nutrition_data}")
    return nutrition_data


def extract_nutrition_from_analysis_with_validation(analysis_text, food_description):
    """從分析文本中提取營養數據，並進行合理性檢查（保留原本份量校正）"""
    import re
    
    print(f"🔍 DEBUG - 開始份量校正分析：{food_description}")
    
    # 先使用改良的基本提取函數
    nutrition_data = extract_nutrition_from_analysis(analysis_text)
    
    # 🔧 保留原本的合理性檢查：對常見食物進行驗證
    food_lower = food_description.lower()
    
    # 檢測是否有份量描述
    portion_keywords = ['杯', 'ml', 'cc', '毫升', '份', '個', '片', '碗', '盤', '條', '根']
    has_portion = any(keyword in food_description for keyword in portion_keywords)
    
    print(f"🔍 DEBUG - 是否有份量描述：{has_portion}")

    # 🔧 保留：豆漿合理性檢查（現在預設330ml）
    if '豆漿' in food_lower:
        if not has_portion:
            # 沒特別說明時，應該是330ml的數據
            if nutrition_data['calories'] > 180:
                print(f"🔍 DEBUG - 豆漿熱量異常：{nutrition_data['calories']}，修正為330ml標準")
                return {'calories': 132, 'carbs': 13, 'protein': 9, 'fat': 5, 'fiber': 3, 'sugar': 10}
        elif '1杯' in food_description or '250ml' in food_description:
            # 明確說1杯或250ml時
            if nutrition_data['calories'] > 150:
                print(f"🔍 DEBUG - 豆漿250ml熱量異常：{nutrition_data['calories']}，修正為250ml標準")
                return {'calories': 100, 'carbs': 10, 'protein': 7, 'fat': 4, 'fiber': 2, 'sugar': 8}
    
    # 🔧 保留：咖啡合理性檢查
    elif '咖啡' in food_lower and '拿鐵' not in food_lower:
        if not has_portion:
            # 黑咖啡330ml
            if nutrition_data['calories'] > 15:
                print(f"🔍 DEBUG - 咖啡熱量異常，修正為330ml標準")
                return {'calories': 7, 'carbs': 1, 'protein': 0, 'fat': 0, 'fiber': 0, 'sugar': 0}
    
    # 🔧 保留：其他飲料類檢查
    elif any(drink in food_lower for drink in ['奶茶', '果汁', '可樂', '汽水']):
        if not has_portion and nutrition_data['calories'] < 50:
            # 可能低估了，330ml的飲料不應該少於50大卡
            print(f"🔍 DEBUG - 飲料熱量過低，使用合理預設值")
            return get_reasonable_nutrition_data(food_description)
    
    elif '水' in food_lower and '果汁' not in food_lower:
        print(f"🔍 DEBUG - 檢測到水，設為0熱量")
        return {'calories': 0, 'carbs': 0, 'protein': 0, 'fat': 0, 'fiber': 0, 'sugar': 0}
    
    # 🔧 保留：通用合理性檢查
    if nutrition_data['calories'] > 1000:  # 單一食物超過1000大卡要檢查
        print(f"🔍 DEBUG - 熱量異常：{nutrition_data['calories']}，食物：{food_description}")
        return get_reasonable_nutrition_data(food_description)
    
    # 🔧 新增：確保所有數值都是有效的
    validated_data = {}
    for key, value in nutrition_data.items():
        try:
            validated_data[key] = float(value) if value is not None else 0.0
        except (ValueError, TypeError):
            validated_data[key] = 0.0
            print(f"⚠️ WARNING - {key} 數值無效，設為0")
    
    print(f"🔧 DEBUG - 份量校正後的最終營養數據：{validated_data}")
    return validated_data    

def test_nutrition_extraction():
    """測試營養數據提取功能"""
    test_analysis = """
🔍 實際攝取分析：
熱量：約720大卡
碳水化合物：28g
蛋白質：35g
脂肪：25g
纖維：4g

💡 這一餐評價：
這餐營養豐富，蛋白質含量充足...
"""
    
    result = extract_nutrition_from_analysis(test_analysis)
    print(f"測試結果：{result}")
    return result

# 🔧 新增：合理營養數據資料庫
def get_reasonable_nutrition_data(food_description):
    """根據食物描述提供合理的營養數據"""
    food_lower = food_description.lower()
    
    # 🔧 新增：檢測份量關鍵字
    portion_keywords = ['杯', 'ml', 'cc', '毫升', '公升', 'l', '份', '個', '片', '碗', '盤', '包', '罐', '瓶', '條']
    has_portion = any(keyword in food_description for keyword in portion_keywords)
    
    print(f"🔍 DEBUG - 食物描述：{food_description}")
    print(f"🔍 DEBUG - 是否有份量描述：{has_portion}")
    
    # 🔧 更新：飲料類營養資料庫（以330ml為基準）
    drink_database = {
        '豆漿': {'calories': 132, 'carbs': 13, 'protein': 9, 'fat': 5, 'fiber': 3, 'sugar': 10},  # 330ml
        '咖啡': {'calories': 7, 'carbs': 1, 'protein': 0, 'fat': 0, 'fiber': 0, 'sugar': 0},      # 330ml 黑咖啡
        '拿鐵': {'calories': 198, 'carbs': 16, 'protein': 11, 'fat': 11, 'fiber': 0, 'sugar': 16}, # 330ml
        '牛奶': {'calories': 198, 'carbs': 16, 'protein': 11, 'fat': 11, 'fiber': 0, 'sugar': 16}, # 330ml
        '奶茶': {'calories': 231, 'carbs': 35, 'protein': 7, 'fat': 8, 'fiber': 0, 'sugar': 30},   # 330ml
        '果汁': {'calories': 145, 'carbs': 36, 'protein': 1, 'fat': 0, 'fiber': 1, 'sugar': 32},   # 330ml 柳橙汁
        '可樂': {'calories': 139, 'carbs': 35, 'protein': 0, 'fat': 0, 'fiber': 0, 'sugar': 35},   # 330ml
        '茶': {'calories': 3, 'carbs': 1, 'protein': 0, 'fat': 0, 'fiber': 0, 'sugar': 0},         # 330ml 無糖茶
        '水': {'calories': 0, 'carbs': 0, 'protein': 0, 'fat': 0, 'fiber': 0, 'sugar': 0}          # 330ml
    }
    
    # 🔧 更新：一般食物營養資料庫（以一份為基準）
    food_database = {
        '白飯': {'calories': 280, 'carbs': 62, 'protein': 6, 'fat': 1, 'fiber': 1, 'sugar': 0},    # 1碗(150g)
        '糙米飯': {'calories': 220, 'carbs': 45, 'protein': 5, 'fat': 2, 'fiber': 4, 'sugar': 0},  # 1碗(150g)
        '雞蛋': {'calories': 70, 'carbs': 1, 'protein': 6, 'fat': 5, 'fiber': 0, 'sugar': 1},      # 1顆(50g)
        '吐司': {'calories': 80, 'carbs': 15, 'protein': 3, 'fat': 1, 'fiber': 2, 'sugar': 2},     # 1片(30g)
        '全麥吐司': {'calories': 80, 'carbs': 15, 'protein': 3, 'fat': 1, 'fiber': 2, 'sugar': 2}, # 1片(30g)
        '雞胸肉': {'calories': 165, 'carbs': 0, 'protein': 31, 'fat': 4, 'fiber': 0, 'sugar': 0},  # 1份(100g)
        '雞腿': {'calories': 250, 'carbs': 0, 'protein': 26, 'fat': 16, 'fiber': 0, 'sugar': 0},   # 1份(100g)
        '魚': {'calories': 140, 'carbs': 0, 'protein': 26, 'fat': 3, 'fiber': 0, 'sugar': 0},      # 1份(100g)
        '豆腐': {'calories': 80, 'carbs': 2, 'protein': 8, 'fat': 5, 'fiber': 1, 'sugar': 1},      # 1塊(100g)
        '香蕉': {'calories': 90, 'carbs': 23, 'protein': 1, 'fat': 0, 'fiber': 3, 'sugar': 12},    # 1根(100g)
        '蘋果': {'calories': 80, 'carbs': 21, 'protein': 0, 'fat': 0, 'fiber': 4, 'sugar': 16},    # 1個(150g)
        '麵包': {'calories': 80, 'carbs': 15, 'protein': 3, 'fat': 1, 'fiber': 2, 'sugar': 2},     # 1片(30g)
        '麵': {'calories': 220, 'carbs': 44, 'protein': 8, 'fat': 1, 'fiber': 2, 'sugar': 2},      # 1份(100g乾重)
        '青菜': {'calories': 25, 'carbs': 5, 'protein': 3, 'fat': 0, 'fiber': 3, 'sugar': 2},      # 1份(100g)
        '沙拉': {'calories': 50, 'carbs': 8, 'protein': 3, 'fat': 1, 'fiber': 4, 'sugar': 4},      # 1份(150g)
    }

    # 🔧 新增：如果沒有份量描述，使用預設份量說明
    portion_note = ""
    if not has_portion:
        portion_note = "（系統預設份量）"
    
    # 優先檢查飲料類
    for keyword, nutrition in drink_database.items():
        if keyword in food_lower:
            adjusted_nutrition = nutrition.copy()
            
            # 如果有特別註明份量，需要調整計算
            if has_portion and ('250ml' in food_description or '1杯' in food_description):
                # 從330ml調整為250ml
                ratio = 250 / 330
                for key in ['calories', 'carbs', 'protein', 'fat', 'fiber', 'sugar']:
                    adjusted_nutrition[key] = round(nutrition[key] * ratio, 1)
                portion_note = "（250ml）"
            elif not has_portion:
                portion_note = "（預設330ml）"
            
            print(f"🔍 DEBUG - 飲料匹配：{keyword} = {adjusted_nutrition} {portion_note}")
            return adjusted_nutrition
    
    # 檢查一般食物
    for keyword, nutrition in food_database.items():
        if keyword in food_lower:
            adjusted_nutrition = nutrition.copy()
            
            if not has_portion:
                portion_note = "（預設1份）"
            
            print(f"🔍 DEBUG - 食物匹配：{keyword} = {adjusted_nutrition} {portion_note}")
            return adjusted_nutrition
    
    # 🔧 新增：如果沒有匹配到任何食物，根據描述推測類型
    if any(drink_word in food_lower for drink_word in ['汁', '茶', '咖啡', '奶', '水', '飲', '可樂', '汽水']):
        # 推測為飲料類，使用330ml基準
        default_nutrition = {'calories': 100, 'carbs': 15, 'protein': 2, 'fat': 2, 'fiber': 1, 'sugar': 12}
        print(f"🔍 DEBUG - 推測為飲料類：{default_nutrition}（預設330ml）")
        return default_nutrition
    else:
        # 推測為一般食物，使用1份基準
        default_nutrition = {'calories': 150, 'carbs': 20, 'protein': 8, 'fat': 5, 'fiber': 2, 'sugar': 5}
        print(f"🔍 DEBUG - 推測為一般食物：{default_nutrition}（預設1份）")
        return default_nutrition

def determine_meal_type(description):
    """判斷餐型"""
    description_lower = description.lower()
    
    if any(word in description_lower for word in ['早餐', '早上', '早飯', 'morning']):
        return '早餐'
    elif any(word in description_lower for word in ['午餐', '中午', '午飯', 'lunch']):
        return '午餐'
    elif any(word in description_lower for word in ['晚餐', '晚上', '晚飯', 'dinner']):
        return '晚餐'
    elif any(word in description_lower for word in ['點心', '零食', '下午茶', 'snack']):
        return '點心'
    else:
        return '餐點'


def generate_detailed_meal_suggestions(user, recent_meals, food_preferences):
    """API 不可用時的詳細餐點建議"""
    
    health_goal = user[7] if user[7] else "維持健康"
    restrictions = user[8] if user[8] else "無"
    
    suggestions = f"""根據你的健康目標「{health_goal}」，推薦以下餐點：

🥗 均衡餐點建議（含精確份量）：

🍽️ 選項1：蒸魚餐
- 糙米飯：1碗 = 1拳頭大 = 約180g = 約220大卡
- 蒸鮭魚：1片 = 手掌大厚度 = 約120g = 約180大卡  
- 炒青菜：1份 = 煮熟後100g = 約30大卡
- 橄欖油：1茶匙 = 5ml = 約45大卡
📊 總熱量：約475大卡

🍽️ 選項2：雞胸肉沙拉
- 雞胸肉：1份 = 手掌大 = 約100g = 約165大卡
- 生菜沙拉：2碗 = 約200g = 約30大卡
- 全麥麵包：1片 = 約30g = 約80大卡
- 堅果：1湯匙 = 約15g = 約90大卡
📊 總熱量：約365大卡

💡 份量調整原則：
• 減重：減少主食至半碗（90g）
• 增重：增加蛋白質至1.5份（150g）
• 控糖：選擇低GI主食，控制在100g以內

⚠️ 飲食限制考量：{restrictions}

詳細營養分析功能暫時無法使用，以上為精確份量建議。"""
    
    return suggestions


def generate_detailed_food_consultation(question, user):
    """API 不可用時的詳細食物諮詢"""
    
    consultation = f"""關於你的問題「{question}」：

💡 一般建議與份量指示：

🔸 基本原則：
• 任何食物都要適量攝取
• 注意個人健康狀況
• 均衡飲食最重要

🔸 常見食物份量參考：
• 水果：1份 = 1個拳頭大 = 約150g
• 堅果：1份 = 1湯匙 = 約30g  
• 全穀物：1份 = 1拳頭 = 約150-200g
• 蛋白質：1份 = 1手掌厚度 = 約100-120g

⚠️ 特別提醒：
• 如有特殊疾病，請諮詢醫師
• 注意個人過敏原
• 逐漸調整份量，避免突然改變

📋 建議做法：
• 使用食物秤確認重量
• 學會視覺估量
• 記錄飲食反應

詳細營養諮詢功能暫時無法使用，建議諮詢專業營養師獲得個人化建議。"""
    
    return consultation


class ReminderSystem:
    """提醒系統"""
    
    @staticmethod
    def send_daily_reminder():
        """發送每日提醒"""
        try:
            # 這裡可以添加主動提醒邏輯
            print("每日提醒系統運行中...")
        except Exception as e:
            print(f"發送提醒失敗: {e}")
    
    @staticmethod
    def send_profile_update_reminder():
        """發送個人資料更新提醒"""
        try:
            # 這裡可以添加更新提醒邏輯
            print("個人資料更新提醒系統運行中...")
        except Exception as e:
            print(f"發送更新提醒失敗: {e}")

class EmailReporter:
    """Email 報告系統"""
    
    @staticmethod
    def generate_daily_report():
        """生成每日使用者報告"""
        try:
            # 這裡可以添加每日報告邏輯
            print("每日報告生成中...")
        except Exception as e:
            print(f"發送報告失敗：{e}")


def schedule_tasks():
    """排程任務"""
    import schedule
    import time
    
    # 每日9點發送提醒
    schedule.every().day.at("09:00").do(ReminderSystem.send_daily_reminder)
    
    # 每月1號發送更新提醒（改為每30天）
    schedule.every(1).days.do(ReminderSystem.send_profile_update_reminder)
    
    # 每日23點發送使用報告
    schedule.every().day.at("23:00").do(EmailReporter.generate_daily_report)
    
    while True:
        schedule.run_pending()
        time.sleep(60)

def start_scheduler():
    """啟動排程器"""
    import threading
    try:
        scheduler_thread = threading.Thread(target=schedule_tasks)
        scheduler_thread.daemon = True
        scheduler_thread.start()
        print("排程系統已啟動")
    except Exception as e:
        print(f"排程系統啟動失敗：{e}")



def generate_weekly_report(event):
    user_id = event.source.user_id
    user = UserManager.get_user(user_id)
    
    if not user:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="請先設定個人資料才能產生週報告。")
        )
        return
    
    # 取得本週飲食記錄
    weekly_meals = UserManager.get_weekly_meals(user_id)
    
    if not weekly_meals:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="本週還沒有飲食記錄。開始記錄你的飲食，下週就能看到詳細報告了！")
        )
        return
    
    # 生成週報告
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # 準備本週飲食資料
        meals_by_type = {}
        for meal in weekly_meals:
            meal_type = meal[0]
            if meal_type not in meals_by_type:
                meals_by_type[meal_type] = []
            meals_by_type[meal_type].append(meal[1])
        
        meals_summary = ""
        for meal_type, meals in meals_by_type.items():
            meals_summary += f"\n{meal_type}：\n"
            for meal in meals[:3]:  # 只顯示前3個
                meals_summary += f"- {meal}\n"
        
        user_data = get_user_data(user)
        diabetes_context = f"糖尿病類型：{user_data['diabetes_type']}" if user_data['diabetes_type'] else "無糖尿病"

        user_context = f"""
用戶資料：{user_data['name']}，{user_data['age']}歲，{user_data['gender']}
身高：{user_data['height']}cm，體重：{user_data['weight']}kg
活動量：{user_data['activity_level']}
健康目標：{user_data['health_goals']}
飲食限制：{user_data['dietary_restrictions']}
{diabetes_context}

記錄期間：{record_days}天（共{total_meals}餐）
餐型分佈：{dict(meal_counts)}

詳細飲食記錄：
{meals_summary}
""" 
        
        report_prompt = """
作為專業營養師，請為用戶生成本週營養分析報告：

1. 本週飲食總結與亮點
2. 營養攝取評估（優點與不足）
3. 與健康目標的對比分析
4. 具體改善建議（3-5點）
5. 下週飲食規劃建議

請提供具體、實用的建議，語調要專業而親切。
"""
        
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": report_prompt},
                {"role": "user", "content": user_context}
            ],
            max_tokens=1000,
            temperature=0.7
        )
        
        report = response.choices[0].message.content
        
        final_report = f"""📊 本週營養分析報告
記錄天數：{len(set(meal[3][:10] for meal in weekly_meals))} 天
總餐數：{len(weekly_meals)} 餐

{report}

💡 持續記錄飲食，讓我為你提供更準確的營養建議！"""
        
    except Exception as e:
        final_report = f"""📊 本週營養記錄摘要
記錄天數：{len(set(meal[3][:10] for meal in weekly_meals))} 天
總餐數：{len(weekly_meals)} 餐

🎯 飲食記錄統計：
"""
        
        # 統計餐型分佈
        meal_counts = {}
        for meal in weekly_meals:
            meal_type = meal[0]
            meal_counts[meal_type] = meal_counts.get(meal_type, 0) + 1
        
        for meal_type, count in meal_counts.items():
            final_report += f"• {meal_type}：{count} 次\n"
        
        final_report += f"""
💡 一般建議：
• 保持規律的三餐時間
• 增加蔬果攝取
• 注意營養均衡
• 適量補充水分

詳細分析功能暫時無法使用，請稍後再試。"""
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=final_report)
    )

def show_instructions(event):
    instructions = """📋 使用說明

🏥 我是20年經驗營養師，特別專精糖尿病醣類控制

🔹 主要功能：
📝 記錄飲食：輸入「記錄飲食」或直接描述食物
🍽️ 飲食建議：「今天晚餐吃什麼？」
❓ 食物諮詢：「糖尿病可以吃水果嗎？」
📊 營養追蹤：「今日進度」查看即時攝取
📈 週報告：「週報告」追蹤營養趨勢

🔹 記錄飲食格式：
• 包含餐型：早餐/午餐/晚餐/點心
• 描述食物：具體的食物名稱
• 註明份量：碗數、片數、杯數等

💬 記錄範例：
• 「早餐吃了蛋餅一份加豆漿一杯」
• 「午餐：雞腿便當，有滷蛋和高麗菜」
• 「下午喝了拿鐵咖啡中杯」

🔹 智慧對話範例：
• 「不知道要吃什麼」→ 推薦適合餐點
• 「香蕉適合我嗎？」→ 個人化食物建議
• 「血糖高能吃什麼？」→ 糖尿病專業建議

🔹 快速指令：
• 輸入「取消」可隨時取消操作
• 輸入「重新啟動」可重置對話狀態
• 輸入「記錄飲食」開始記錄引導

💡 小技巧：
越詳細的描述，越準確的建議！"""
    
    quick_reply = QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="📝 記錄飲食", text="記錄飲食")),
        QuickReplyButton(action=MessageAction(label="📊 今日進度", text="今日進度")),
        QuickReplyButton(action=MessageAction(label="🍽️ 飲食建議", text="飲食建議")),
        QuickReplyButton(action=MessageAction(label="📈 週報告", text="週報告"))
    ])
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=instructions, quick_reply=quick_reply)
    )

@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    guide_text = """📸 感謝你上傳照片！

為了提供更準確的分析，請用文字描述你的食物：

💬 描述範例：
• 「白飯一碗 + 紅燒豬肉 + 青菜」
• 「雞腿便當，有滷蛋和高麗菜」
• 「拿鐵咖啡中杯 + 全麥吐司」

🤖 或者你可以問我：
• 「這個便當適合減重嗎？」
• 「推薦健康的午餐」
• 「糖尿病可以吃什麼？」

我會根據你的個人資料提供最適合的建議！"""
    
    quick_reply = QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="今日進度", text="今日進度")),
        QuickReplyButton(action=MessageAction(label="飲食建議", text="等等可以吃什麼？")),
        QuickReplyButton(action=MessageAction(label="食物諮詢", text="這個食物適合我嗎？"))
    ])
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=guide_text, quick_reply=quick_reply)
    )

def provide_meal_suggestions(event, user_message=""):
    """提供飲食建議"""
    user_id = event.source.user_id
    user = UserManager.get_user(user_id)
    
    if not user:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="請先設定個人資料，我才能提供適合你的飲食建議喔！")
        )
        return
    
    try:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="🤔 讓我想想適合你的餐點...")
        )
        
        # 取得用戶最近飲食和偏好
        recent_meals = UserManager.get_recent_meals(user_id)
        food_preferences = UserManager.get_food_preferences(user_id)
        
        # 安全地處理用戶資料，避免 None 值和索引錯誤
        user_data = get_user_data(user)
        name = user_data['name']
        age = user_data['age']
        gender = user_data['gender']
        height = user_data['height']
        weight = user_data['weight']
        activity = user_data['activity_level']
        goals = user_data['health_goals']
        restrictions = user_data['dietary_restrictions']
        body_fat = user_data['body_fat_percentage']
        diabetes = user_data['diabetes_type']
        target_cal = user_data['target_calories']
        target_carbs = user_data['target_carbs']
        target_protein = user_data['target_protein']
        target_fat = user_data['target_fat']
        
        # 安全地格式化字串
        diabetes_context = f"糖尿病類型：{diabetes}" if diabetes else "無糖尿病"
        
        user_context = f"""
用戶資料：{name}，{age}歲，{gender}
身高：{height}cm，體重：{weight}kg，體脂率：{body_fat:.1f}%
活動量：{activity}
健康目標：{goals}
飲食限制：{restrictions}
{diabetes_context}

每日營養目標：
熱量：{target_cal:.0f}大卡，碳水：{target_carbs:.0f}g，蛋白質：{target_protein:.0f}g，脂肪：{target_fat:.0f}g

最近3天飲食：
{chr(10).join([f"- {meal[0]}" for meal in recent_meals[:5]])}

常吃食物：
{chr(10).join([f"- {pref[0]} (吃過{pref[1]}次)" for pref in food_preferences[:5]])}

用戶詢問：{user_message}
"""
        
        # 修改後的建議 Prompt
        suggestion_prompt = """
你是擁有20年經驗的專業營養師。請根據用戶的飲食習慣提供建議。

重要原則：
1. 基於用戶實際的飲食記錄，不假設標準三餐模式
2. 考慮用戶可能不是每天三餐的飲食習慣
3. 提供彈性的用餐建議

根據用戶最近的實際飲食記錄：
{chr(10).join([f"- {meal[0]}" for meal in recent_meals[:5]])}

請提供：
🍽️ 適合現在吃的餐點選項（2-3個）

每個選項包含：
- 具體食物和份量
- 熱量估算
- 為什麼適合現在吃
- 簡單製作方式

💡 彈性用餐建議：
- 依照個人節奏進食
- 餓了再吃，不需強迫三餐
- 重視營養品質勝過餐數

請提供實用建議，不要預設用戶的用餐時間表。
"""
        
        # 使用 OpenAI 生成建議
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": suggestion_prompt},
                    {"role": "user", "content": user_context}
                ],
                max_tokens=1200,
                temperature=0.8
            )
            
            suggestions = response.choices[0].message.content
            
        except Exception as openai_error:
            suggestions = generate_detailed_meal_suggestions(user, recent_meals, food_preferences)
        
        line_bot_api.push_message(
            event.source.user_id,
            TextSendMessage(text=f"🍽️ 為你推薦的餐點：\n\n{suggestions}")
        )
        
    except Exception as e:
        error_message = f"抱歉，推薦功能出現問題：{str(e)}\n\n請稍後再試或直接詢問特定餐點建議。"
        
        line_bot_api.push_message(
            event.source.user_id,
            TextSendMessage(text=error_message)
        )

def provide_food_consultation(event, user_question):
    """提供食物諮詢"""
    user_id = event.source.user_id
    user = UserManager.get_user(user_id)
    
    try:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="🤔 讓我分析一下這個問題...")
        )
        
        # 準備用戶背景資訊 - 安全處理資料
        if user:
            user_data = get_user_data(user)
            name = user_data['name']
            age = user_data['age']
            gender = user_data['gender']
            height = user_data['height']
            weight = user_data['weight']
            activity = user_data['activity_level']
            goals = user_data['health_goals']
            restrictions = user_data['dietary_restrictions']
            body_fat = user_data['body_fat_percentage']
            diabetes = user_data['diabetes_type']
            
            diabetes_context = f"糖尿病類型：{diabetes}" if diabetes else "無糖尿病"
            user_context = f"""
用戶資料：{name}，{age}歲，{gender}
身高：{height}cm，體重：{weight}kg，體脂率：{body_fat:.1f}%
活動量：{activity}
健康目標：{goals}
飲食限制：{restrictions}
{diabetes_context}
"""
        else:
            user_context = "用戶未設定個人資料，請提供一般性建議。"
        
        # 修改後的諮詢 Prompt
        consultation_prompt = f"""
你是擁有20年經驗的專業營養師，特別專精糖尿病醣類控制。請回答用戶關於食物的問題：

{user_context}

重要要求：如果涉及份量建議，必須提供明確的份量指示

請使用以下份量參考：
🍚 主食: 1碗飯 = 1拳頭 = 150-200g
🥩 蛋白質: 1份肉類 = 1手掌大小厚度 = 100-120g  
🥬 蔬菜: 1份 = 煮熟後100g = 生菜200g
🥜 堅果: 1份 = 30g = 約1湯匙
🥛 飲品: 1杯 = 250ml

糖尿病患者特別考量：
- 重點關注血糖影響
- 提供GI值參考
- 建議適合的食用時間
- 給出血糖監測建議

請提供：
1. 直接回答用戶的問題（可以吃/不建議/適量等）
2. 說明原因（營養成分、健康影響）  
3. 如果可以吃，明確建議份量：
   - 具體重量（克數）
   - 視覺比對（拳頭/手掌/湯匙等）
   - 建議頻率（每天/每週幾次）
   - 最佳食用時間
4. 如果不建議，提供份量明確的替代選項
5. 針對用戶健康狀況的特別提醒

請用專業但易懂的語言回應，讓用戶能精確執行建議。
"""
        
        # 使用 OpenAI 分析
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": consultation_prompt},
                    {"role": "user", "content": f"用戶問題：{user_question}"}
                ],
                max_tokens=800,
                temperature=0.7
            )
            
            consultation_result = response.choices[0].message.content
            
        except Exception as openai_error:
            consultation_result = generate_detailed_food_consultation(user_question, user)
        
        line_bot_api.push_message(
            event.source.user_id,
            TextSendMessage(text=f"💡 營養師建議：\n\n{consultation_result}")
        )
        
    except Exception as e:
        error_message = f"抱歉，諮詢功能出現問題：{str(e)}\n\n請重新描述你的問題，我會盡力回答。"
        
        line_bot_api.push_message(
            event.source.user_id,
            TextSendMessage(text=error_message)
        )

def analyze_food_description(event, food_description):
    user_id = event.source.user_id
    user = UserManager.get_user(user_id)
    
    try:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="🔍 正在分析你的飲食內容，請稍候...")
        )
        
        # 判斷餐型
        meal_type = determine_meal_type(food_description)
        
        # 建立個人化提示 - 安全處理資料
        if user:
            user_data = get_user_data(user)
            name = user_data['name']
            age = user_data['age']
            gender = user_data['gender']
            height = user_data['height']
            weight = user_data['weight']
            activity = user_data['activity_level']
            goals = user_data['health_goals']
            restrictions = user_data['dietary_restrictions']
            body_fat = user_data['body_fat_percentage']
            diabetes = user_data['diabetes_type']
            target_cal = user_data['target_calories']
            target_carbs = user_data['target_carbs']
            target_protein = user_data['target_protein']
            target_fat = user_data['target_fat']
            
            diabetes_context = f"糖尿病類型：{diabetes}" if diabetes else "無糖尿病"
            user_context = f"""
用戶資料：
- 姓名：{name}，{age}歲，{gender}
- 身高：{height}cm，體重：{weight}kg，體脂率：{body_fat:.1f}%
- 活動量：{activity}
- 健康目標：{goals}
- 飲食限制：{restrictions}
- {diabetes_context}

每日營養目標：
熱量：{target_cal:.0f}大卡，碳水：{target_carbs:.0f}g，蛋白質：{target_protein:.0f}g，脂肪：{target_fat:.0f}g
"""
        else:
            user_context = "用戶未設定個人資料，請提供一般性建議。"
        
        # 修改後的營養分析 Prompt
        nutrition_prompt = f"""
你是一位擁有20年經驗的專業營養師，特別專精糖尿病醣類控制。請根據用戶實際吃的食物進行分析。

{user_context}

重要原則：
1. 只分析用戶實際描述的食物，不要添加或建議其他餐點
2. 對於常見食物要使用準確的營養數據
3. 🔧 新增：份量預設規則
   - 飲料類（豆漿、咖啡、奶茶、果汁等）：沒特別註明時以 330ml 計算
   - 一般食物：沒特別註明時以 1份 計算
   - 如果用戶有明確說明份量，則以用戶描述為準
4. 使用純文字格式，多用表情符號

🥛 飲料類（330ml基準）：
• 豆漿：熱量132大卡，碳水13g，蛋白質9g，脂肪5g
• 咖啡（黑咖啡）：熱量7大卡，碳水1g，蛋白質0g，脂肪0g
• 拿鐵：熱量198大卡，碳水16g，蛋白質11g，脂肪11g
• 奶茶：熱量231大卡，碳水35g，蛋白質7g，脂肪8g
• 果汁：熱量145大卡，碳水36g，蛋白質1g，脂肪0g

🍚 主食類（1份基準）：
• 白飯1碗(150g)：熱量280大卡，碳水62g，蛋白質6g，脂肪1g
• 糙米飯1碗(150g)：熱量220大卡，碳水45g，蛋白質5g，脂肪2g
• 全麥吐司1片(30g)：熱量80大卡，碳水15g，蛋白質3g，脂肪1g

🥩 蛋白質類（1份基準）：
• 雞蛋1顆(50g)：熱量70大卡，碳水1g，蛋白質6g，脂肪5g
• 雞胸肉1份(100g)：熱量165大卡，碳水0g，蛋白質31g，脂肪4g
• 魚類1份(100g)：熱量140大卡，碳水0g，蛋白質26g，脂肪3g

🥬 蔬菜類（1份基準）：
• 青菜1份(100g)：熱量25大卡，碳水5g，蛋白質3g，脂肪0g
• 沙拉1份(150g)：熱量50大卡，碳水8g，蛋白質3g，脂肪1g

🍎 水果類（1份基準）：
• 香蕉1根(100g)：熱量90大卡，碳水23g，蛋白質1g，脂肪0g
• 蘋果1個(150g)：熱量80大卡，碳水21g，蛋白質0g，脂肪0g

份量判斷規則：
- 如果用戶說「豆漿」沒特別說明 → 預設330ml
- 如果用戶說「雞蛋」沒特別說明 → 預設1顆
- 如果用戶說「飯」沒特別說明 → 預設1碗
- 如果用戶明確說「豆漿1杯」→ 以250ml計算
- 如果用戶明確說「雞蛋2顆」→ 以2顆計算

請提供：

請提供：

🔍 實際攝取分析：
只分析用戶描述的這一餐，包括：
熱量：約XX大卡
碳水化合物：XXg
蛋白質：XXg
脂肪：XXg
纖維：XXg

💡 這一餐評價：
基於用戶健康目標評估這餐是否合適
這餐的優點和可改進之處
對血糖的影響（如有糖尿病）

🍽️ 下次進食建議：
當用戶想吃下一餐時的建議
適合的食物類型和份量
與這餐的營養互補

特別注意：
不要建議用戶"今天還需要吃什麼來補足營養"
不要假設一天必須吃三餐
只針對實際吃的食物給建議
尊重用戶的飲食節奏
嚴格按照份量預設規則提供數據
在分析中明確說明使用的份量假設
確保營養數據的合理性

請確保在回應中清楚標示各營養素的數值，格式如：熱量：300大卡，碳水化合物：45g
"""
        
        # 使用 OpenAI 分析
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": nutrition_prompt},
                    {"role": "user", "content": f"請分析以下{meal_type}：{food_description}"}
                ],
                max_tokens=1000,
                temperature=0.7
            )
            
            analysis_result = response.choices[0].message.content
            
            # 從分析結果中提取營養數據
            nutrition_data = extract_nutrition_from_analysis(analysis_result)
            
            # 儲存飲食記錄（包含營養數據）
            UserManager.save_meal_record(user_id, meal_type, food_description, analysis_result, nutrition_data)
            
            # 添加記錄確認訊息
            confirmation_text = f"""

✅ 已記錄你的{meal_type}

📝 記錄內容：{food_description}
📊 營養數據：
熱量：{nutrition_data.get('calories', 0):.0f} 大卡
碳水：{nutrition_data.get('carbs', 0):.1f}g
蛋白質：{nutrition_data.get('protein', 0):.1f}g
脂肪：{nutrition_data.get('fat', 0):.1f}g

💡 輸入「今日進度」可查看累計營養攝取"""
            
            # 組合完整回應
            full_response = f"🍽️ {meal_type}營養分析：\n\n{analysis_result}{confirmation_text}"
            
        except Exception as openai_error:
            analysis_result = f"OpenAI 分析暫時無法使用：{str(openai_error)}\n\n請確保 API 額度充足，或稍後再試。"
            
            # 使用基本營養數據
            nutrition_data = {'calories': 300, 'carbs': 45, 'protein': 15, 'fat': 10, 'fiber': 5, 'sugar': 8}
            
            # 仍然儲存記錄
            UserManager.save_meal_record(user_id, meal_type, food_description, analysis_result, nutrition_data)
            
            confirmation_text = f"""

✅ 已記錄你的{meal_type}

📝 記錄內容：{food_description}
📊 使用預估營養數據

💡 輸入「今日進度」可查看累計營養攝取"""
            
            full_response = f"{analysis_result}{confirmation_text}"
        
        line_bot_api.push_message(
            event.source.user_id,
            TextSendMessage(text=full_response)
        )
        
    except Exception as e:
        error_message = f"抱歉，分析出現問題：{str(e)}\n\n請重新描述你的飲食內容。"
        
        line_bot_api.push_message(
            event.source.user_id,
            TextSendMessage(text=error_message)
        )


def extract_nutrition_from_analysis(analysis_text):
    """從分析文本中提取營養數據"""
    import re
    
    # 改進的正則表達式提取
    calories_patterns = [
        r'熱量[:：]\s*約?(\d+(?:\.\d+)?)\s*大卡',
        r'總熱量[:：]\s*約?(\d+(?:\.\d+)?)\s*大卡',
        r'(\d+(?:\.\d+)?)\s*大卡'
    ]
    
    carbs_patterns = [
        r'碳水化合物[:：]\s*約?(\d+(?:\.\d+)?)\s*g',
        r'碳水[:：]\s*約?(\d+(?:\.\d+)?)\s*g'
    ]
    
    protein_patterns = [
        r'蛋白質[:：]\s*約?(\d+(?:\.\d+)?)\s*g'
    ]
    
    fat_patterns = [
        r'脂肪[:：]\s*約?(\d+(?:\.\d+)?)\s*g'
    ]
    
    def extract_value(patterns, text, default=0):
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    return float(match.group(1))
                except:
                    continue
        return default
    
    calories = extract_value(calories_patterns, analysis_text, 300)
    carbs = extract_value(carbs_patterns, analysis_text, 45)
    protein = extract_value(protein_patterns, analysis_text, 15)
    fat = extract_value(fat_patterns, analysis_text, 10)
    
    return {
        'calories': calories,
        'carbs': carbs,
        'protein': protein,
        'fat': fat,
        'fiber': 5,  # 預設值
        'sugar': 8   # 預設值
    }

def get_daily_progress_summary(user_id):
    """取得每日進度簡要"""
    user = UserManager.get_user(user_id)
    daily_nutrition = UserManager.get_daily_nutrition(user_id)
    
    if not user or not daily_nutrition:
        return ""
    
    user_data = get_user_data(user)
    current_calories = daily_nutrition[3] or 0
    target_calories = user_data['target_calories']
    
    remaining_calories = max(0, target_calories - current_calories)
    progress_percent = (current_calories / target_calories * 100) if target_calories > 0 else 0
    
    return f"""
📊 今日進度更新：
目前攝取：{current_calories:.0f} / {target_calories:.0f} 大卡 ({progress_percent:.0f}%)
還需要：{remaining_calories:.0f} 大卡

可以說「今日進度」查看詳細營養追蹤！"""

def generate_weekly_report(event):
    user_id = event.source.user_id
    user = UserManager.get_user(user_id)
    
    if not user:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="請先設定個人資料才能產生週報告。")
        )
        return
    
    # 取得本週飲食記錄
    weekly_meals = UserManager.get_weekly_meals(user_id)
    
    if not weekly_meals:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="本週還沒有飲食記錄。開始記錄你的飲食，就能看到詳細報告了！")
        )
        return
    
    # 計算統計數據
    unique_dates = set(meal[3][:10] for meal in weekly_meals)  # 取日期部分
    record_days = len(unique_dates)
    total_meals = len(weekly_meals)
    
    # 統計餐型分佈
    meal_counts = {}
    meal_details = []
    for meal in weekly_meals:
        meal_type = meal[0]
        meal_desc = meal[1]
        meal_date = meal[3][:10]  # 取日期
        meal_time = meal[3][11:16]  # 取時間
        
        meal_counts[meal_type] = meal_counts.get(meal_type, 0) + 1
        meal_details.append(f"{meal_date} {meal_time} {meal_type}：{meal_desc}")
    
    # 生成增強版報告
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # 準備詳細的飲食資料
        meals_by_date = {}
        for meal in weekly_meals:
            date = meal[3][:10]
            if date not in meals_by_date:
                meals_by_date[date] = []
            meals_by_date[date].append(f"{meal[0]}：{meal[1]}")
        
        meals_summary = ""
        for date, meals in sorted(meals_by_date.items()):
            meals_summary += f"\n📅 {date}：\n"
            for meal in meals:
                meals_summary += f"  • {meal}\n"
        
        # 安全取得用戶資料
        user_data = get_user_data(user)
        name = user_data['name']
        age = user_data['age']
        gender = user_data['gender']
        height = user_data['height']
        weight = user_data['weight']
        activity = user_data['activity_level']
        goals = user_data['health_goals']
        restrictions = user_data['dietary_restrictions']
        diabetes = user_data['diabetes_type']
    
        diabetes_context = f"糖尿病類型：{diabetes}" if diabetes else "無糖尿病"
    
        user_context = f"""
用戶資料：{name}，{age}歲，{gender}
身高：{height}cm，體重：{weight}kg
活動量：{activity}
健康目標：{goals}
飲食限制：{restrictions}
{diabetes_context}

記錄期間：{record_days}天（共{total_meals}餐）
餐型分佈：{dict(meal_counts)}

詳細飲食記錄：
{meals_summary}
"""
        
        report_prompt = """
作為專業營養師，請為用戶生成飲食分析報告（即使記錄天數不滿7天）：

重要原則：
1. 基於實際記錄天數分析，不需要7天才能分析
2. 使用純文字格式，多用表情符號
3. 不要使用 # *  等符號

請提供：

🔍 記錄期間飲食分析：
分析用戶在記錄期間的飲食模式
評估營養攝取的均衡性
指出飲食的優點和需要改善的地方

💡 個人化建議：
基於用戶健康目標提供具體建議
針對糖尿病患者提供血糖控制建議（如適用）
考慮用戶的飲食限制和偏好

🎯 具體改善方向：
3-5個實用的改善建議
每個建議要包含具體的執行方法
建議的食物選擇和份量

📈 未來飲食規劃：
下週的飲食重點
如何逐步改善飲食習慣
長期健康目標的達成策略

🏆 鼓勵與肯定：
肯定用戶開始記錄飲食的行為
鼓勵持續記錄和改善

請提供實用、正面、專業的建議，讓用戶感受到進步和鼓勵。
"""
        
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": report_prompt},
                {"role": "user", "content": user_context}
            ],
            max_tokens=1200,
            temperature=0.7
        )
        
        ai_analysis = response.choices[0].message.content
        
        # 組合完整報告
        final_report = f"""📊 飲食分析報告

⏰ 記錄期間：{record_days} 天
🍽️ 總餐數：{total_meals} 餐
📈 平均每日：{total_meals/record_days:.1f} 餐

🥘 餐型統計：
"""
        
        for meal_type, count in meal_counts.items():
            percentage = (count / total_meals * 100)
            final_report += f"• {meal_type}：{count} 次 ({percentage:.0f}%)\n"
        
        final_report += f"\n{ai_analysis}\n\n💪 持續記錄飲食，讓我為你提供更準確的營養建議！"
        
    except Exception as e:
        print(f"AI分析失敗：{e}")
        
        # 備用詳細報告
        final_report = f"""📊 飲食記錄分析報告

⏰ 記錄期間：{record_days} 天
🍽️ 總餐數：{total_meals} 餐
📈 平均每日：{total_meals/record_days:.1f} 餐

🥘 餐型統計：
"""
        
        for meal_type, count in meal_counts.items():
            percentage = (count / total_meals * 100)
            final_report += f"• {meal_type}：{count} 次 ({percentage:.0f}%)\n"
        
        final_report += f"""

📅 最近記錄：
"""
        
        # 顯示最近5筆記錄
        for meal in weekly_meals[:5]:
            date = meal[3][:10]
            time = meal[3][11:16]
            final_report += f"• {date} {time} {meal[0]}：{meal[1][:30]}{'...' if len(meal[1]) > 30 else ''}\n"
        
        if len(weekly_meals) > 5:
            final_report += f"• 還有 {len(weekly_meals)-5} 筆記錄...\n"
        
        final_report += f"""

💡 基於你的記錄建議：

🎯 飲食模式觀察：
- 記錄了 {record_days} 天的飲食，平均每天 {total_meals/record_days:.1f} 餐
- 最常記錄的是 {max(meal_counts, key=meal_counts.get)}

📈 改善建議：
- 持續記錄有助於了解飲食習慣
- 試著增加蔬菜和蛋白質的攝取
- 保持規律的用餐時間
"""
        
        if diabetes:
            final_report += "• 糖尿病患者建議少量多餐，注意血糖監測\n"
        
        final_report += f"""
🏆 很棒的開始！
記錄飲食是健康管理的第一步，你已經在正確的道路上了！

💪 繼續加油，我會陪伴你達成健康目標！"""
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=final_report)
    )


def show_user_profile(event):
    user_id = event.source.user_id
    user = UserManager.get_user(user_id)  # 添加這行
    
    if not user:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="你還沒有設定個人資料。請先點選「設定個人資料」。")
        )
        return
    
    user_data = get_user_data(user)

    bmi = user_data['weight'] / ((user_data['height'] / 100) ** 2)
    body_fat = user_data['body_fat_percentage']
    
    profile_text = f"""👤 你的個人資料：

- 姓名：{user_data['name']}
- 年齡：{user_data['age']} 歲  
- 性別：{user_data['gender']}
- 身高：{user_data['height']} cm
- 體重：{user_data['weight']} kg
- 體脂率：{user_data['body_fat_percentage']:.1f}%
- BMI：{bmi:.1f}
- 活動量：{user_data['activity_level']}
- 健康目標：{user_data['health_goals']}
- 飲食限制：{user_data['dietary_restrictions']}"""
    
    if user_data['diabetes_type']:
        profile_text += f"\n• 糖尿病類型：{user_data['diabetes_type']}"
    
    profile_text += f"""

🎯 每日營養目標：
- 熱量：{user_data['target_calories']:.0f} 大卡
- 碳水：{user_data['target_carbs']:.0f} g
- 蛋白質：{user_data['target_protein']:.0f} g
- 脂肪：{user_data['target_fat']:.0f} g

💡 想要更新資料，請點選「更新個人資料」。"""
    
    quick_reply = QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="更新個人資料", text="更新個人資料")),
        QuickReplyButton(action=MessageAction(label="今日進度", text="今日進度"))
    ])
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=profile_text, quick_reply=quick_reply)
    )

def show_instructions(event):
    instructions = """📋 使用說明

🏥 我是20年經驗營養師，特別專精糖尿病醣類控制

🔹 主要功能：
📝 記錄飲食：「早餐吃了蛋餅加豆漿」
🍽️ 飲食建議：「今天晚餐吃什麼？」
❓ 食物諮詢：「糖尿病可以吃水果嗎？」
📊 營養追蹤：即時顯示今日進度
📈 週報告：追蹤營養趨勢

🔹 智慧對話範例：
• 「不知道要吃什麼」→ 推薦適合餐點
• 「香蕉適合我嗎？」→ 個人化食物建議
• 「這個份量OK嗎？」→ 份量調整建議
• 「血糖高能吃什麼？」→ 糖尿病專業建議

🔹 體脂率精準計算：
✓ 智能估算或實測輸入
✓ Katch-McArdle 公式計算代謝
✓ 個人化營養目標制定

🔹 糖尿病專業功能：
🩺 醣類攝取精確控制
📉 血糖影響評估
🍽️ 低GI食物推薦
⏰ 用餐時機建議

🔹 個人化功能：
✓ 記住你的身體資料和體脂率
✓ 根據健康目標精準建議
✓ 避免你的飲食禁忌
✓ 學習你的飲食偏好
✓ 主動關心提醒

💡 小技巧：
越詳細的描述，越準確的建議！"""
    
    quick_reply = QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="今日進度", text="今日進度")),
        QuickReplyButton(action=MessageAction(label="飲食建議", text="今天要吃什麼？")),
        QuickReplyButton(action=MessageAction(label="食物諮詢", text="糖尿病可以吃燕麥嗎？")),
        QuickReplyButton(action=MessageAction(label="記錄飲食", text="午餐吃了雞腿便當"))
    ])
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=instructions, quick_reply=quick_reply)
    )

@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    guide_text = """📸 感謝你上傳照片！

為了提供更準確的分析，請用文字描述你的食物：

💬 描述範例：
• 「白飯一碗 + 紅燒豬肉 + 青菜」
• 「雞腿便當，有滷蛋和高麗菜」
• 「拿鐵咖啡中杯 + 全麥吐司」

🩺 糖尿病患者特別注意：
• 「糙米飯半碗 + 蒸魚一片」
• 「燕麥粥一碗，無糖」

🤖 或者你可以問我：
• 「這個便當適合糖尿病患者嗎？」
• 「推薦低GI的午餐」
• 「血糖高可以吃什麼？」

我會根據你的個人資料和體脂率提供最適合的建議！"""
    
    quick_reply = QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="飲食建議", text="推薦健康餐點")),
        QuickReplyButton(action=MessageAction(label="糖尿病諮詢", text="血糖高可以吃什麼？")),
        QuickReplyButton(action=MessageAction(label="今日進度", text="今日進度"))
    ])
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=guide_text, quick_reply=quick_reply)
    )

def check_database_structure():
    """檢查並修正資料庫結構"""
    conn = None
    try:
        conn = sqlite3.connect('nutrition_bot.db', timeout=20.0)
        cursor = conn.cursor()
        
        # 檢查 meal_records 表結構
        cursor.execute("PRAGMA table_info(meal_records)")
        meal_columns = [column[1] for column in cursor.fetchall()]
        print(f"🔍 DEBUG - meal_records 現有欄位：{meal_columns}")
        
        # 檢查 daily_nutrition 表結構  
        cursor.execute("PRAGMA table_info(daily_nutrition)")
        daily_columns = [column[1] for column in cursor.fetchall()]
        print(f"🔍 DEBUG - daily_nutrition 現有欄位：{daily_columns}")
        
        # 確保營養素欄位存在
        required_nutrition_columns = ['calories', 'carbs', 'protein', 'fat', 'fiber', 'sugar']
        
        for column in required_nutrition_columns:
            if column not in meal_columns:
                try:
                    cursor.execute(f'ALTER TABLE meal_records ADD COLUMN {column} REAL DEFAULT 0')
                    print(f"✅ 已添加 meal_records.{column}")
                except sqlite3.OperationalError as e:
                    print(f"❌ 添加 meal_records.{column} 失敗：{e}")
        
        conn.commit()
        print("✅ 資料庫結構檢查完成")
        
    except Exception as e:
        print(f"❌ 資料庫結構檢查失敗：{e}")
    finally:
        if conn:
            conn.close()    

def determine_meal_type(description):
    """判斷餐型"""
    description_lower = description.lower()
    
    if any(word in description_lower for word in ['早餐', '早上', '早飯', 'morning', '晨間']):
        return '早餐'
    elif any(word in description_lower for word in ['午餐', '中午', '午飯', 'lunch', '中餐']):
        return '午餐'
    elif any(word in description_lower for word in ['晚餐', '晚上', '晚飯', 'dinner', '晚食']):
        return '晚餐'
    elif any(word in description_lower for word in ['點心', '零食', '下午茶', 'snack', '宵夜']):
        return '點心'
    else:
        return '餐點'

def generate_detailed_meal_suggestions(user, recent_meals, food_preferences):
    """API 不可用時的詳細餐點建議"""
    
    user_data = get_user_data(user)
    health_goal = user_data['health_goals']
    restrictions = user_data['dietary_restrictions']
    diabetes_type = user_data['diabetes_type']

    suggestions = f"""根據你的健康目標「{health_goal}」，推薦以下餐點：

🥗 均衡餐點建議（含精確份量）：

選項1：蒸魚餐
• 糙米飯：1碗 = 1拳頭大 = 約180g = 約220大卡
• 蒸鮭魚：1片 = 手掌大厚度 = 約120g = 約180大卡  
• 炒青菜：1份 = 煮熟後100g = 約30大卡
• 橄欖油：1茶匙 = 5ml = 約45大卡
總熱量：約475大卡

選項2：雞胸肉沙拉
• 雞胸肉：1份 = 手掌大 = 約100g = 約165大卡
• 生菜沙拉：2碗 = 約200g = 約30大卡
• 全麥麵包：1片 = 約30g = 約80大卡
• 堅果：1湯匙 = 約15g = 約90大卡
總熱量：約365大卡"""
    
    if diabetes_type:
        suggestions += f"""

🩺 糖尿病專用餐點：

選項3：低GI控糖餐
• 燕麥：1/2碗 = 約50g乾重 = 約180大卡
• 水煮蛋：2顆 = 約100g = 約140大卡
• 花椰菜：1份 = 約150g = 約40大卡
• 酪梨：1/4顆 = 約50g = 約80大卡
總熱量：約440大卡，低GI值"""
    
    suggestions += f"""

💡 份量調整原則：
• 減重：減少主食至半碗（90g）
• 增重：增加蛋白質至1.5份（150g）
• 控糖：選擇低GI主食，控制在100g以內

⚠️ 飲食限制考量：{restrictions}

詳細營養分析功能暫時無法使用，以上為精確份量建議。"""
    
    return suggestions

def generate_detailed_food_consultation(question, user):
    """API 不可用時的詳細食物諮詢"""
    
    diabetes_note = ""
    if user:
        user_data = get_user_data(user)
        if user_data['diabetes_type']:
            diabetes_note = f"\n🩺 糖尿病患者特別注意：由於你有{user_data['diabetes_type']}，建議特別注意血糖監測。"
        else:
            diabetes_note = ""
    else:
        diabetes_note = ""
    
    consultation = f"""關於你的問題「{question}」：

💡 一般建議與份量指示：

🔸 基本原則：
• 任何食物都要適量攝取
• 注意個人健康狀況
• 均衡飲食最重要
• 糖尿病患者特別注意醣類控制

🔸 常見食物份量參考：
• 水果：1份 = 1個拳頭大 = 約150g
• 堅果：1份 = 1湯匙 = 約30g  
• 全穀物：1份 = 1拳頭 = 約150-200g
• 蛋白質：1份 = 1手掌厚度 = 約100-120g

🔸 糖尿病友特別份量建議：
• 水果：每次不超過1份，餐後2小時食用
• 主食：每餐不超過1碗（150g）
• 選擇低GI食物優先

⚠️ 特別提醒：
• 如有特殊疾病，請諮詢醫師
• 注意個人過敏原
• 逐漸調整份量，避免突然改變{diabetes_note}

📋 建議做法：
• 使用食物秤確認重量
• 學會視覺估量
• 記錄飲食反應
• 定期監測血糖（糖尿病患者）

詳細營養諮詢功能暫時無法使用，建議諮詢專業營養師獲得個人化建議。"""
    
    return consultation

def keep_alive():
    """保持服務活躍"""
    while True:
        try:
            time.sleep(600)  # 等待10分鐘
            # 請把下面的網址改成你的Render網址
            requests.get("https://nutrition-linebot.onrender.com")
            print("Keep alive ping sent")
        except:
            pass

@app.route("/health", methods=['GET'])
def health_check():
    return "OK", 200


if __name__ == "__main__":
    import os

    # 檢查是否在本地開發環境
    is_local = os.getenv('RENDER') is None

    if is_local:
        print("🔧 本地開發模式")
        print("📋 可用功能測試：")
        print("- 資料庫連線測試")
        print("- OpenAI API 測試") 
        print("- 基本功能測試")
        print()
        
        # 只啟動基本服務，不啟動 keep_alive 和 scheduler
        port = int(os.environ.get('PORT', 5000))
        print(f"🚀 本地伺服器啟動在 http://localhost:{port}")
        app.run(host='127.0.0.1', port=port, debug=True)
    else:
        # 啟動排程器
        keep_alive_thread = threading.Thread(target=keep_alive)
        keep_alive_thread.daemon = True
        keep_alive_thread.start()
        start_scheduler()
        check_database_structure()
        startup_database_maintenance()
        
        test_nutrition_extraction()
        port = int(os.environ.get('PORT', 5000))
        print(f"啟動20年經驗糖尿病專業營養師機器人在端口 {port}")
        print("主要功能：")
        print("- 體脂率精準計算與營養目標制定")
        print("- 糖尿病醣類控制專業建議")
        print("- 每日營養追蹤與進度顯示")
        print("- 主動提醒與月度更新提醒")
        print("- 每日使用報告Email發送")
        app.run(host='0.0.0.0', port=port, debug=True)