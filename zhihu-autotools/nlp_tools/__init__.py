from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

FONT_PATH = "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"
STOP_WORDS_PATH = Path(__file__).parent / "stop_words.txt"

with open(STOP_WORDS_PATH, "r", encoding="utf-8") as f:
    STOP_WORDS = set(line.strip() for line in f if line.strip())


fm.fontManager.addfont(FONT_PATH)
cjk_font_name = fm.FontProperties(fname=FONT_PATH).get_name()

plt.rcParams['font.sans-serif'] = ['Arial', cjk_font_name, 'sans-serif']
plt.rcParams['axes.unicode_minus'] = False
