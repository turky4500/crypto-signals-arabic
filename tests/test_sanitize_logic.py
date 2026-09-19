"""تحقق من منطق عقّام النشر في monitor.yml (نسخة معزولة قابلة للتنفيذ محليًا).

لا يحاكي خطوة العمل في Actions فقط، بل يثبت أن الملف الملوّث بعلامات صراع
git يُنظَّف ليصبح JSON صالحًا قبل نشره على الموقع.
"""
import glob
import json
import os
import re
import tempfile

MARK = re.compile(r"^(<{7}|={7,}|>{7})( |$)", re.M)


def sanitize_dir(data_dir: str) -> tuple[int, list[str]]:
    """نسخة من سكربت 'Assemble static site' في monitor.yml."""
    dropped = 0
    notes: list[str] = []
    for p in glob.glob(os.path.join(data_dir, "*.json")):
        try:
            raw = open(p, encoding="utf-8").read()
        except OSError as exc:
            notes.append(f"SKIP unreadable {p}: {exc}")
            continue
        if MARK.search(raw):
            notes.append(f"FIX conflict markers -> {os.path.basename(p)}")
            try:
                json.loads(raw)
            except Exception:
                lines = [ln for ln in raw.splitlines() if not MARK.match(ln)]
                raw = "\n".join(lines)
        try:
            json.loads(raw)
        except Exception as exc:
            notes.append(f"DROP corrupt {os.path.basename(p)}: {str(exc)[:80]}")
            os.remove(p)
            dropped += 1
        else:
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(raw)
    return dropped, notes


def test_conflict_status_cleans_to_valid_json(tmp_path):
    corrupt = (
        '{\n'
        '  "binance_connected": true,\n'
        '  "monitoring_active": true,\n'
        '  "whatsapp_connected": true,\n'
        '<<<<<<< HEAD\n'
        '  "last_update": 1789840803804,\n'
        '  "run_id": "3002",\n'
        '=======\n'
        '  "last_update": 1789841244715,\n'
        '  "run_id": "3004",\n'
        '>>>>>>> 62adefa (chore(data): \\u062a\\u062d\\u062f\\u064a\\u062b \\u0628\\u064a\\u0627\\u0646\\u0627\\u062a \\u0627\\u0644\\u0645\\u0631\\u0627\\u0642\\u0628\\u0629 \\u0648\\u0627\\u0644\\u0625\\u0634\\u0627\\u0631\\u0627\\u062a)\n'
        '  "performance": {"total": 75}\n'
        '}\n'
    )
    p = tmp_path / "status.json"
    p.write_text(corrupt, encoding="utf-8")

    dropped, notes = sanitize_dir(str(tmp_path))

    assert dropped == 0, notes
    assert any("FIX conflict markers" in n for n in notes), notes
    data = json.loads(p.read_text(encoding="utf-8"))
    # بعد إزالة أسطر العلامات، تتكرر المفاتيح؛ يبقى آخرها (run 3004 = الأحدث)
    assert data["run_id"] == "3004"
    assert data["last_update"] == 1789841244715
    assert data["performance"]["total"] == 75


def test_valid_file_untouched(tmp_path):
    p = tmp_path / "ok.json"
    p.write_text('{"a": 1, "b": [1, 2, 3]}', encoding="utf-8")

    dropped, notes = sanitize_dir(str(tmp_path))

    assert dropped == 0
    assert not notes
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1, "b": [1, 2, 3]}


def test_truly_broken_file_dropped(tmp_path):
    p = tmp_path / "garbage.json"
    p.write_text('{"a": ', encoding="utf-8")

    dropped, notes = sanitize_dir(str(tmp_path))

    assert dropped == 1
    assert not os.path.exists(p)