import sqlite3
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "database.db")


def get_connection():
    return sqlite3.connect(DB_PATH)


# ==============================
# DB初期化（拡張版）
# ==============================
def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # ==============================
    # 候補者テーブル
    # ==============================
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS candidates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        -- 基本情報
        name TEXT,
        age INTEGER,
        nearest TEXT,
        company TEXT,
        annual_income INTEGER,

        -- 稼働条件
        price INTEGER,
        start TEXT,
        remote TEXT,
        location TEXT,

        -- 経験フラグ（検索用）
        exp_dev INTEGER,
        exp_infra INTEGER,
        exp_ops INTEGER,
        exp_pmo INTEGER,
        exp_requirements INTEGER,
        exp_design INTEGER,
        exp_test INTEGER,
        exp_operation INTEGER,

        -- 上流・リーダー
        has_upstream INTEGER,
        has_leader INTEGER,
        has_client_contact INTEGER,

        -- 総経験年数
        total_experience_months INTEGER,
        total_experience_years INTEGER,

        -- そのまま残す（互換用）
        skills TEXT,
        role TEXT,

        -- 管理
        sender_company TEXT,
        sender_name TEXT,
        mail_path TEXT,
        attachment_paths TEXT,
        source TEXT,

        review_status TEXT,
        review_reason TEXT,
        
        vendor_type TEXT,

        created_at TEXT,
        updated_at TEXT
    )
    """)

    # ==============================
    # スキルマスタ
    # ==============================
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS skills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE
    )
    """)

    # ==============================
    # 候補者スキル（中間）
    # ==============================
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS candidate_skills (
        candidate_id INTEGER,
        skill_id INTEGER,
        category TEXT,
        years INTEGER,
        last_used TEXT,

        FOREIGN KEY(candidate_id) REFERENCES candidates(id),
        FOREIGN KEY(skill_id) REFERENCES skills(id)
    )
    """)

    conn.commit()
    conn.close()
    add_total_experience_columns()
    add_review_columns()
    add_vendor_type_column()

def add_review_columns():
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "ALTER TABLE candidates ADD COLUMN review_status TEXT"
        )
    except:
        pass

    try:
        cursor.execute(
            "ALTER TABLE candidates ADD COLUMN review_reason TEXT"
        )
    except:
        pass

    conn.commit()
    conn.close()

def add_vendor_type_column():

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "ALTER TABLE candidates ADD COLUMN vendor_type TEXT"
        )
    except:
        pass

    conn.commit()
    conn.close()


# ==============================
# スキルID取得 or 作成
# ==============================
def get_or_create_skill(cursor, skill_name):
    cursor.execute("SELECT id FROM skills WHERE name = ?", (skill_name,))
    row = cursor.fetchone()

    if row:
        return row[0]

    cursor.execute("INSERT INTO skills (name) VALUES (?)", (skill_name,))
    return cursor.lastrowid


# ==============================
# 候補者登録（拡張版）
# ==============================
def insert_candidate(data, source="text"):
    conn = get_connection()
    cursor = conn.cursor()

    now = datetime.now().isoformat()

    # ==============================
    # candidates挿入
    # ==============================
    cursor.execute("""
    INSERT INTO candidates (
        name, age, nearest, company, annual_income,
        price, start, remote, location,
        exp_dev, exp_infra, exp_ops, exp_pmo,
        exp_requirements, exp_design, exp_test, exp_operation,
        has_upstream, has_leader, has_client_contact,
        total_experience_months,
        total_experience_years,
        skills, role,
        sender_company, sender_name, mail_path, attachment_paths, source,
        review_status, review_reason,
        vendor_type,
        created_at, updated_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data.get("name"),
        data.get("age"),
        data.get("nearest"),
        data.get("company"),
        data.get("annual_income"),

        data.get("price"),
        data.get("start"),
        data.get("remote"),
        data.get("location"),

        int(data.get("exp_dev", 0)),
        int(data.get("exp_infra", 0)),
        int(data.get("exp_ops", 0)),
        int(data.get("exp_pmo", 0)),

        int(data.get("exp_requirements", 0)),
        int(data.get("exp_design", 0)),
        int(data.get("exp_test", 0)),
        int(data.get("exp_operation", 0)),

        int(data.get("has_upstream", 0)),
        int(data.get("has_leader", 0)),
        int(data.get("has_client_contact", 0)),

        data.get("total_experience_months"),
        data.get("total_experience_years"),

        ",".join(data.get("skills", [])),
        ",".join(data.get("role", [])),

        data.get("sender_company"),
        data.get("sender_name"),
        data.get("mail_path"),
        data.get("attachment_paths"),

        source,

        data.get("review_status"),
        data.get("review_reason"),

        data.get("vendor_type"),

        now,
        now
    ))

    candidate_id = cursor.lastrowid

    # ==============================
    # スキル登録（中間テーブル）
    # ==============================
    for skill in data.get("skills", []):

        # ==============================
        # dict対応（AIスキル形式）
        # ==============================
        if isinstance(skill, dict):
            skill_name = skill.get("name")
            category = skill.get("category")
            years = skill.get("years")
        else:
            skill_name = skill
            category = None
            years = None

        if not skill_name:
            continue

        skill_id = get_or_create_skill(cursor, skill_name)

        cursor.execute("""
        INSERT INTO candidate_skills (
            candidate_id, skill_id, category, years, last_used
        )
        VALUES (?, ?, ?, ?, ?)
        """, (
            candidate_id,
            skill_id,
            category,
            years,
            None
        ))

    conn.commit()
    conn.close()


# ==============================
# 一覧取得
# ==============================
def get_all_candidates():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT
        id,
        name,
        age,
        price,
        nearest,
        company,
        start,
        remote,
        total_experience_years,
        skills,
        role,

        exp_dev,
        exp_infra,
        exp_ops,
        exp_pmo,

        exp_requirements,
        exp_design,
        exp_test,

        has_upstream,
        has_leader,
        has_client_contact,

        source,

        created_at,
        updated_at,
        attachment_paths,
        review_status,
        review_reason,
        vendor_type

    FROM candidates
    ORDER BY id DESC
    """)

    rows = cursor.fetchall()

    conn.close()
    return rows

def add_total_experience_columns():
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "ALTER TABLE candidates ADD COLUMN total_experience_months INTEGER"
        )
    except:
        pass

    try:
        cursor.execute(
            "ALTER TABLE candidates ADD COLUMN total_experience_years INTEGER"
        )
    except:
        pass

    conn.commit()
    conn.close()

def get_candidate_by_id(candidate_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT *
    FROM candidates
    WHERE id = ?
    """, (candidate_id,))

    row = cursor.fetchone()

    conn.close()

    return row

def find_duplicate_candidate(name, nearest):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT id
    FROM candidates
    WHERE name = ?
      AND nearest = ?
    """, (name, nearest))

    row = cursor.fetchone()

    conn.close()

    return row

def find_duplicate_candidates(name, nearest):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    SELECT
        *
    FROM candidates
    WHERE name = ?
      AND nearest = ?
    ORDER BY id
    """, (name, nearest))

    rows = cursor.fetchall()

    conn.close()

    return rows

def update_review_status(candidate_id):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    UPDATE candidates
    SET review_status = 'approved',
        review_reason = ''
    WHERE id = ?
    """, (candidate_id,))

    conn.commit()
    conn.close()

def delete_candidate(candidate_id):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    DELETE FROM candidates
    WHERE id = ?
    """, (candidate_id,))

    conn.commit()
    conn.close()

def update_candidate(data):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    UPDATE candidates
    SET
        name = ?,
        age = ?,
        nearest = ?,
        company = ?,
        vendor_type = ?,

        price = ?,
        start = ?,
        remote = ?,

        skills = ?,
        role = ?,
        total_experience_years = ?,
                   
        exp_dev = ?,
        exp_infra = ?,
        exp_ops = ?,
        exp_pmo = ?,

        exp_requirements = ?,
        exp_design = ?,
        exp_test = ?,

        has_upstream = ?,
        has_leader = ?,
        has_client_contact = ?,
      
        review_status = ?,
        review_reason = ?,

        updated_at = ?

    WHERE id = ?
    """, (
        data.get("name"),
        data.get("age"),
        data.get("nearest"),
        data.get("company"),
        data.get("vendor_type"),

        data.get("price"),
        data.get("start"),
        data.get("remote"),

        data.get("skills"),
        data.get("role"),
        data.get("total_experience_years"),

        data.get("exp_dev"),
        data.get("exp_infra"),
        data.get("exp_ops"),
        data.get("exp_pmo"),

        data.get("exp_requirements"),
        data.get("exp_design"),
        data.get("exp_test"),

        data.get("has_upstream"),
        data.get("has_leader"),
        data.get("has_client_contact"),

        data.get("review_status"),
        data.get("review_reason"),


        data.get("updated_at"),

        data.get("id")
    ))

    conn.commit()
    conn.close()