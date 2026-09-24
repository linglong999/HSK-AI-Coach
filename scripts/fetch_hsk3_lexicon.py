# ============================================================
# 拉取 HSK 3.0（2025）词表/字表源（B8）
# 真源 : krmanik/HSK-3.0（CC BY-SA 4.0）
#      - New HSK (2025)/HSK Words      /HSK_Level_{1..4}_words.txt    （词表，逐词一行）
#      - New HSK (2025)/HSK Hanzi      /HSK_Level_{1..4}_hanzi.txt    （认读字，逐字一行）
#      - New HSK (2025)/HSK Handwritten/HSK_Level_{1-2|3|4}_handwritten.txt（书写字，1-2 合并）
# 交叉核对 : elkmovie/hsk30（MIT）
#      - wordlist.txt（分级词表，序号+词）
#      - charlist.txt （分级字表，序号+字）
# 产物固化到 datasets/hsk30_raw/krmanik_lexicon/ 与 datasets/hsk30_raw/elkmovie/。
# 由 datasets/convert_lexicon_3.py 消费 → lexicon_hsk3_2025.json。
# 运行：python scripts/fetch_hsk3_lexicon.py
# ============================================================
import os
import sys
import time
import urllib.request

_RO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RO not in sys.path:
    sys.path.insert(0, _RO)

_RAW = os.path.join(_RO, "datasets", "hsk30_raw")
_KM = os.path.join(_RAW, "krmanik_lexicon")
_EL = os.path.join(_RAW, "elkmovie")

_UA = {"User-Agent": "trae"}

# krmanik 词表：分级独立文件（词）
_KM_WORDS = {
    "HSK_Level_1_words.txt": 1, "HSK_Level_2_words.txt": 2,
    "HSK_Level_3_words.txt": 3, "HSK_Level_4_words.txt": 4,
}
# krmanik 认读字：分级独立文件
_KM_HANZI = {
    "HSK_Level_1_hanzi.txt": 1, "HSK_Level_2_hanzi.txt": 2,
    "HSK_Level_3_hanzi.txt": 3, "HSK_Level_4_hanzi.txt": 4,
}
# krmanik 书写字：1-2 合并一个文件（拍板：不拆级，整文件标级2）
_KM_HAND = {
    "HSK_Level_1-2_handwritten.txt": 2, "HSK_Level_3_handwritten.txt": 3,
    "HSK_Level_4_handwritten.txt": 4,
}
# elkmovie 交叉核对源
_EL_FILES = ["wordlist.txt", "charlist.txt"]

_BASE = "https://raw.githubusercontent.com/krmanik/HSK-3.0/main/New%20HSK%20(2025)"
_WORD_DIR = "HSK%20Words"
_HANZI_DIR = "HSK%20Hanzi"
_HAND_DIR = "HSK%20Handwritten"
_EL_BASE = "https://raw.githubusercontent.com/elkmovie/hsk30/main"


def _fetch(url: str) -> bytes:
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=_UA)
            data = urllib.request.urlopen(req, timeout=30).read()
            if len(data) < 20:
                raise ValueError("empty body")
            return data
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5)
    raise RuntimeError(f"fetch failed after 3 attempts: {url} -> {last}")


def _pull(name_map, subdir, outdir):
    """按 {file:level} 从 krmanik 对应子目录拉取。"""
    for fn, lv in name_map.items():
        url = f"{_BASE}/{subdir}/{fn}"
        data = _fetch(url)
        path = os.path.join(outdir, f"L{lv}_{fn}")
        with open(path, "wb") as f:
            f.write(data)
        n = len(data.decode("utf-8").strip().splitlines())
        print(f"  {path}  lv={lv}  lines={n}")


def main():
    os.makedirs(_KM, exist_ok=True)
    os.makedirs(_EL, exist_ok=True)
    print("== krmanik 词表 ==")
    _pull(_KM_WORDS, _WORD_DIR, _KM)
    print("== krmanik 认读字 ==")
    _pull(_KM_HANZI, _HANZI_DIR, _KM)
    print("== krmanik 书写字 ==")
    _pull(_KM_HAND, _HAND_DIR, _KM)
    print("== elkmovie 交叉核对源 ==")
    for fn in _EL_FILES:
        data = _fetch(f"{_EL_BASE}/{fn}")
        with open(os.path.join(_EL, fn), "wb") as f:
            f.write(data)
        print(f"  {fn}  bytes={len(data)} lines={len(data.decode('utf-8').splitlines())}")


if __name__ == "__main__":
    main()