# config 包：集中配置（settings.py 用 python-dotenv 加载 .env）。
# 必须是包（含 __init__.py）——pyproject 包发现含 config*，pip install -e .
# 后才能保证任意 CWD 下 `from config import settings` 可用（0.32 治理 CWD 依赖）。