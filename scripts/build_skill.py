"""Build one portable DE Arsenal skill from the maintained Claude plugin guides."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/de-arsenal"
SKILLS = PLUGIN / "skills"


def build(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.write(PLUGIN / "portable/SKILL.md", "de-arsenal/SKILL.md")
        for guide in sorted(SKILLS.glob("*/SKILL.md")):
            _, _, body = guide.read_text(encoding="utf-8").split("---\n", 2)
            archive.writestr(f"de-arsenal/references/{guide.parent.name}.md", body.lstrip())
        archive.write(
            SKILLS / "ingest/scripts/profile_file.py", "de-arsenal/scripts/profile_file.py"
        )
        for name in ("Dockerfile", "github-actions.yml"):
            path = Path("examples/deploy") / name
            archive.write(ROOT / path, f"de-arsenal/{path}")


if __name__ == "__main__":
    target = ROOT / "dist/de-arsenal.zip"
    build(target)
    print(target)
