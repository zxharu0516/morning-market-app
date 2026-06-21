from datetime import datetime
from pathlib import Path
import json
from app.config import REPORTS_DIR
from app.recommendations import format_stock_signals


def save_report(report: dict) -> dict:
    out_dir = Path(REPORTS_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    json_path = out_dir / f"market_report_{stamp}.json"
    md_path = out_dir / f"market_report_{stamp}.md"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md = []
    md.append(report.get("column", ""))
    md.append("\n---\n")
    md.append(report.get("points", ""))
    md.append("\n---\n")
    md.append(report.get("quiz", ""))
    md.append("\n---\n")
    md.append(format_stock_signals(report.get("stock_signals", [])))
    md.append("\n---\n")
    md.append("# 参照ニュース\n")
    for n in report.get("news", []):
        md.append(f"- [{n.get('source')}] {n.get('title')}  ")
        if n.get("link"):
            md.append(f"  {n.get('link')}\n")
    md_path.write_text("\n".join(md), encoding="utf-8")

    return {"json": str(json_path), "markdown": str(md_path)}
