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
            cursor.execute('''
                SELECT * FROM daily_nutrition WHERE user_id = ? AND date = ?
            ''', (user_id, date))
            result = cursor.fetchone()
            return result
        except Exception as e:
            print(f"取得每日營養總結錯誤：{e}")
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
            
            # 檢查表格是否有營養素欄位
            cursor.execute("PRAGMA table_info(meal_records)")
            columns = [column[1] for column in cursor.fetchall()]
            
            has_nutrition_columns = all(col in columns for col in ['calories', 'carbs', 'protein', 'fat', 'fiber', 'sugar'])
            
            if has_nutrition_columns and nutrition_data:
                # 如果有營養素欄位，儲存完整數據
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
            else:
                # 如果沒有營養素欄位，只儲存基本數據
                cursor.execute('''
                    INSERT INTO meal_records 
                    (user_id, meal_type, meal_description, nutrition_analysis)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, meal_type, meal_description, analysis))
            
            conn.commit()
            print(f"已儲存飲食記錄：{meal_type} - {meal_description}")
            
            # 在同一個連線中更新其他數據
            if nutrition_data:
                UserManager._update_daily_nutrition_with_conn(conn, user_id, nutrition_data)
            UserManager._update_food_preferences_with_conn(conn, user_id, meal_description)
            
            conn.commit()
            
        except sqlite3.OperationalError as e:
            if conn:
                conn.rollback()
            print(f"資料庫操作錯誤：{e}")
            raise e
        except Exception as e:
            if conn:
                conn.rollback()
            print(f"儲存記錄時發生錯誤：{e}")
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
            
            # 檢查並創建 daily_nutrition 表
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
            
        except Exception as e:
            print(f"更新每日營養總結失敗：{e}")

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
    
    # 處理個人資料設定流程
    if user_states[user_id]['step'] != 'normal':
        handle_profile_setup_flow(event, message_text)
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
    else:
        # 分析用戶意圖
        intent = MessageAnalyzer.detect_intent(message_text)
        
        if intent == 'suggestion':
            provide_meal_suggestions(event, message_text)
        elif intent == 'consultation':
            provide_food_consultation(event, message_text)
        else:
            # 預設為記錄飲食
            analyze_food_description(event, message_text)

def handle_welcome(event):
    user_id = event.source.user_id
    user = UserManager.get_user(user_id)
    
    if user:
        user_data = get_user_data(user)
        name = user_data['name'] if user_data['name'] else "朋友"
        welcome_text = f"""👋 歡迎回來，{name}！

我是你的專屬AI營養師，可以：

📝 記錄飲食：「早餐吃了燕麥粥」
🍽️ 推薦餐點：「今天晚餐吃什麼？」
❓ 食物諮詢：「糖尿病可以吃香蕉嗎？」
📊 健康追蹤：查看週報告

直接跟我對話就可以了！"""
    else:
        welcome_text = """👋 歡迎使用AI營養師！

我是你的專屬營養師，可以：
📝 記錄分析飲食
🍽️ 推薦適合的餐點  
❓ 回答食物相關問題
📊 提供營養報告

建議先設定個人資料，讓我給你更準確的建議！"""
    
    quick_reply = QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="設定個人資料", text="設定個人資料")),
        QuickReplyButton(action=MessageAction(label="飲食建議", text="飲食建議")),
        QuickReplyButton(action=MessageAction(label="週報告", text="週報告")),
        QuickReplyButton(action=MessageAction(label="使用說明", text="使用說明"))
    ])
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=welcome_text, quick_reply=quick_reply)
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
            QuickReplyButton(action=MessageAction(label="飲食建議", text="今天晚餐吃什麼？")),
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
        
        # 建立個人化提示
        if user:
            user_context = f"""
用戶資料：
- 姓名：{user[1]}，{user[2]}歲，{user[3]}
- 身高：{user[4]}cm，體重：{user[5]}kg
- 活動量：{user[6]}
- 健康目標：{user[7]}
- 飲食限制：{user[8]}
"""
        else:
            user_context = "用戶未設定個人資料，請提供一般性建議。"
        
        # 修改後的營養分析 Prompt
        nutrition_prompt = f"""
你是一位擁有20年經驗的專業營養師，特別專精糖尿病醣類控制。請根據用戶實際吃的食物進行分析。

{user_context}

重要原則：
1. 只分析用戶實際描述的食物，不要添加或建議其他餐點
2. 不要假設用戶一天吃三餐，只分析這一餐
3. 基於實際攝取提供建議，不要補足未吃的餐點
4. 使用純文字格式，多用表情符號

份量參考標準：
🍚 主食: 1碗 = 1拳頭大 = 150-200g = 200-250大卡
🥩 蛋白質: 1份 = 1手掌大厚度 = 100-120g = 120-200大卡
🥬 蔬菜: 1份 = 煮熟100g = 生菜200g = 25-50大卡
🥜 堅果: 1份 = 30g = 1湯匙 = 180大卡
🍎 水果: 1份 = 1個拳頭大 = 150g = 60-100大卡

糖尿病患者特別分析：
重點分析血糖影響、計算醣類含量、評估GI值影響、建議血糖監測時機

🔍 實際攝取分析：
只分析用戶描述的這一餐，包括：
- 估算熱量、碳水化合物、蛋白質、脂肪、纖維
- 各食物分別的營養貢獻
- 這餐的營養密度評估

💡 這一餐評價：
- 基於用戶健康目標評估這餐是否合適
- 這餐的優點和可改進之處
- 對血糖的影響（如有糖尿病）

🍽️ 下次進食建議：
當用戶想吃下一餐時，建議：
- 適合的食物類型和份量
- 與這餐的營養互補
- 具體的食物選擇

⚠️ 特別注意：
- 不要建議用戶"今天還需要吃什麼來補足營養"
- 不要假設一天必須吃三餐
- 只針對實際吃的食物給建議
- 尊重用戶的飲食節奏

回應格式範例：
🔍 你這餐吃的營養分析

📊 實際攝取：
熱量：約300大卡
碳水化合物：35g
蛋白質：20g
脂肪：8g

💡 這餐評價：
蛋白質比例很好，有助肌肉維持
碳水適中，不會造成血糖急升

🍽️ 下次想吃的時候：
可以選擇蔬菜類，補充纖維和維生素
建議份量：綠葉蔬菜 150g（約1.5份）

請只分析實際吃的食物，不要添加建議餐點。

請用純文字格式，多用表情符號，讓回應清晰易讀。
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
            
            # 儲存飲食記錄
            UserManager.save_meal_record(user_id, meal_type, food_description, analysis_result)
            
        except Exception as openai_error:
            analysis_result = f"OpenAI 分析暫時無法使用：{str(openai_error)}\n\n請確保 API 額度充足，或稍後再試。"
        
        line_bot_api.push_message(
            event.source.user_id,
            TextSendMessage(text=f"🍽️ {meal_type}營養分析：\n\n{analysis_result}")
        )
        
    except Exception as e:
        error_message = f"抱歉，分析出現問題：{str(e)}\n\n請重新描述你的飲食內容。"
        
        line_bot_api.push_message(
            event.source.user_id,
            TextSendMessage(text=error_message)
        )


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

🔹 主要功能：
📝 記錄飲食：「早餐吃了蛋餅加豆漿」
🍽️ 飲食建議：「今天晚餐吃什麼？」
❓ 食物諮詢：「糖尿病可以吃水果嗎？」
📊 週報告：追蹤營養趨勢

🔹 智慧對話範例：
• 「不知道要吃什麼」→ 推薦適合餐點
• 「香蕉適合我嗎？」→ 個人化食物建議
• 「這個份量OK嗎？」→ 份量調整建議

🔹 個人化功能：
✓ 記住你的身體資料
✓ 根據健康目標建議
✓ 避免你的飲食禁忌
✓ 學習你的飲食偏好

💡 小技巧：
越詳細的描述，越準確的建議！"""
    
    quick_reply = QuickReply(items=[
        QuickReplyButton(action=MessageAction(label="飲食建議", text="今天要吃什麼？")),
        QuickReplyButton(action=MessageAction(label="食物諮詢", text="燕麥適合減重嗎？")),
        QuickReplyButton(action=MessageAction(label="記錄飲食", text="午餐吃了雞腿便當")),
        QuickReplyButton(action=MessageAction(label="週報告", text="週報告"))
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
        QuickReplyButton(action=MessageAction(label="飲食建議", text="今天晚餐吃什麼？")),
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
2. 不要假設用戶一天吃三餐，只分析這一餐
3. 基於實際攝取提供建議，不要補足未吃的餐點
4. 使用純文字格式，多用表情符號，不要使用 # *  等符號

份量參考標準：
🍚 主食: 1碗 = 1拳頭大 = 150-200g = 200-250大卡
🥩 蛋白質: 1份 = 1手掌大厚度 = 100-120g = 120-200大卡
🥬 蔬菜: 1份 = 煮熟100g = 生菜200g = 25-50大卡
🥜 堅果: 1份 = 30g = 1湯匙 = 180大卡
🍎 水果: 1份 = 1個拳頭大 = 150g = 60-100大卡

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
        try:
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
    # 啟動排程器
    keep_alive_thread = threading.Thread(target=keep_alive)
    keep_alive_thread.daemon = True
    keep_alive_thread.start()
    start_scheduler()
    port = int(os.environ.get('PORT', 5000))
    print(f"啟動20年經驗糖尿病專業營養師機器人在端口 {port}")
    print("主要功能：")
    print("- 體脂率精準計算與營養目標制定")
    print("- 糖尿病醣類控制專業建議")
    print("- 每日營養追蹤與進度顯示")
    print("- 主動提醒與月度更新提醒")
    print("- 每日使用報告Email發送")
    app.run(host='0.0.0.0', port=port, debug=True)