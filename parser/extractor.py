import re

# ==============================
# 共通
# ==============================
def find_value(pattern, text, group_index=1):
    match = re.search(pattern, text)
    if not match:
        return None
    return match.group(group_index).strip()

def find_all_values(pattern, text):
    matches = re.findall(pattern, text)
    return [m.strip() for m in matches if m]

def clean_text(value):
    if not value:
        return value
    value = value.replace("】", "").replace("【", "")
    return value.strip()


def clean_name(name):
    if not name:
        return name
    name = clean_text(name)
    name = re.split(r'[@＠]', name)[0]
    return name.strip()


def extract_role_from_line(line):
    parts = re.split(r'[、,，\s]+', line)
    return [p.strip() for p in parts if p.strip()]


# ==============================
# 基本情報
# ==============================
def extract_basic(text):

    name = find_value(r'氏名\s*[:：]?\s*([^\n\r]+)', text)
    name = clean_name(name)

    nearest = find_value(
        r'(?:最寄駅|最寄り駅)\s*[:：]?\s*([^\n\r]+)',
        text
    )
    nearest = clean_text(nearest)

    company = find_value(r'(株式会社[^\n\r]+)', text)

    role_lines = find_all_values(
        r'役割\s*[:：]?\s*([^\n\r]+)',
        text
    )

    role = []
    for line in role_lines:
        role.extend(extract_role_from_line(line))

    return {
        "name": name,
        "age": extract_age(text),
        "nearest": nearest,
        "company": company,
        "annual_income": None,
        "role": list(set(role))
    }

# ==============================
# 年齢
# ==============================
def extract_age(text):
    match = re.search(r'(\d{2})\s*(歳|才)', text)
    if match:
        return int(match.group(1))
    return None


# ==============================
# 稼働条件
# ==============================
def extract_work(text):

    start = clean_text(find_value(r'(稼働|参画)\s*[:：]?\s*([^\n\r]+)', text, 2))

    location = clean_text(find_value(r'(勤務地|最寄)\s*[:：]?\s*([^\n\r]+)', text, 2))

    remote = None
    if "リモート" in text:
        remote = "リモート併用" if "併用" in text else "リモート可"

    return {
        "price": extract_price(text),
        "start": start,
        "remote": remote,
        "location": location
    }


# ==============================
# 単価
# ==============================
def extract_price(text):
    match = re.search(r'(\d{2,3})\s*万', text)
    if match:
        return int(match.group(1))
    return None


# ==============================
# スキル
# ==============================
SKILL_DICT = {
    "language": ["PHP","Python","Java","JavaScript","TypeScript","SQL","HTML","CSS"], 
    "framework": [
        "Laravel","CakePHP","Symfony","Zend",
        "React","Next.js","Angular","Vue"
    ],
    "db": ["MySQL","PostgreSQL","Oracle","SQLServer"],
    "cloud": ["AWS"],
    "os": ["Linux","Windows","CentOS"],
    "middleware": ["Apache"],
    "tool": ["Docker","Git","GitHub","VSCode","Eclipse","Redmine","JIRA"],
    "ai": ["ChatGPT","Copilot"]
}


def extract_skills(text):
    result = {k: [] for k in SKILL_DICT}
    lower = text.lower()

    for category, skills in SKILL_DICT.items():
        for skill in skills:
            if skill.lower() in lower:
                result[category].append(skill)

    for k in result:
        result[k] = list(set(result[k]))

    return result


# ==============================
# 経験
# ==============================
def extract_experience(text):

    process_keywords = [
        "要件定義", "基本設計", "詳細設計", "設計",
        "製造", "開発", "テスト", "運用", "保守"
    ]

    role_keywords = ["PL", "PM", "リーダー"]

    return {
        "process": list(set([k for k in process_keywords if k in text])),
        "role": list(set([k for k in role_keywords if k in text]))
    }


# ==============================
# 管理情報
# ==============================
def extract_management(text):
    return {
        "sender_company": find_value(r'(株式会社[^\n\r]+)', text, 1),
        "sender_name": None
    }


# ==============================
# メイン
# ==============================
def extract_all(text):
    return {
        "basic": extract_basic(text),
        "work_condition": extract_work(text),
        "skills": extract_skills(text),
        "experience": extract_experience(text),
        "management": extract_management(text)
    }
