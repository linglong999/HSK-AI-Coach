"""P3.5 静态界面文案护栏，不执行前端或引入解析依赖。"""
import re
from pathlib import Path

from engine.feedback_selector import REASON_CODES

ROOT = Path(__file__).resolve().parents[1]
# 字符串先匹配，确保URL中的//与字符串里的/*不会误作注释。
TOKENS = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`|//[^\n]*|/\*[\s\S]*?\*/")


def without_comments(text):
    return TOKENS.sub(lambda m: ' ' * len(m[0]) if m[0].startswith(('//', '/*')) else m[0], text)


def dictionary_keys(text, language):
    # 文案字符串里可能有HTML、冒号、花括号；屏蔽后再取对象范围。
    masked = TOKENS.sub(lambda m: ' ' * len(m[0]), text)
    starts = list(re.finditer(rf'\bconst\s+{language}\b[^=]*=\s*\{{|Object\.assign\(\s*{language}\s*,\s*\{{', masked))
    assert starts, f'{language}: no dictionary found'
    keys = set()
    for start in starts:
        end = masked.index('}', start.end())
        keys.update(re.findall(r'\b([A-Za-z_]\w*)\s*:', masked[start.end():end]))
    return keys


def test_learner_components_have_no_hardcoded_cjk():
    for relative in ('pages/Speak.tsx', 'components/OpeningCard.tsx', 'components/ScenePicker.tsx'):
        text = without_comments((ROOT / 'web/src' / relative).read_text(encoding='utf-8'))
        assert not re.search(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]', text), relative


def test_internal_feedback_codes_not_in_frontend():
    forbidden = {'reason_code', 'REASON_CODES', *REASON_CODES}
    for path in (ROOT / 'web/src').rglob('*.tsx'):
        text = without_comments(path.read_text(encoding='utf-8'))
        found = {code for code in forbidden if re.search(rf'\b{re.escape(code)}\b', text)}
        assert not found, f'{path.relative_to(ROOT)}: {sorted(found)}'


def test_i18n_languages_have_same_keys():
    text = (ROOT / 'web/src/lib/i18n.ts').read_text(encoding='utf-8')
    zh, en = dictionary_keys(text, 'zh'), dictionary_keys(text, 'en')
    assert zh == en, f'zh only: {sorted(zh - en)}; en only: {sorted(en - zh)}'
