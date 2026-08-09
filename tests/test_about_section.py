"""
Тесты раздела «О данных» (секция 3 задания): читательский текст без
инженерного жаргона, без упоминания технических деталей пайплайна.
"""

from config import ROOT_DIR

FORBIDDEN_WORDS = ["pipeline", "canonical", "legacy", "join", "coverage validation", "power bi"]


def _about_block_text() -> str:
    html = (ROOT_DIR / "pipeline" / "dashboard_template.html").read_text(encoding="utf-8")
    start = html.index('<section class="about-block">')
    end = html.index("</section>", start)
    return html[start:end]


def test_about_block_has_no_forbidden_technical_words():
    block = _about_block_text().lower()
    hits = [w for w in FORBIDDEN_WORDS if w in block]
    assert hits == [], f"технические термины найдены в разделе 'О данных': {hits}"


def test_about_block_mentions_pseudonyms():
    block = _about_block_text()
    assert "псевдоним" in block.lower()


def test_about_block_mentions_adr_mvp_availability():
    block = _about_block_text().lower()
    assert "adr" in block and "mvp" in block


def test_about_block_mentions_four_rating_stages():
    block = _about_block_text()
    for stage in ["A", "C", "N", "T"]:
        assert f">{stage}<" in block, f"стадия {stage} не найдена как выделенный элемент в разделе 'О данных'"


def test_about_block_does_not_mention_incorrect_power_bi_measures():
    block = _about_block_text().lower()
    assert "некорректн" not in block
    assert "переработан" not in block
