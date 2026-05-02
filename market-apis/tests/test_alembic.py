from pathlib import Path


def test_alembic_baseline_files_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    migration = root / "alembic" / "versions" / "0001_initial_schema.py"

    assert (root / "alembic.ini").exists()
    assert (root / "alembic" / "env.py").exists()
    assert migration.exists()

    contents = migration.read_text()
    assert 'revision = "0001"' in contents
    assert "def upgrade()" in contents
    assert "def downgrade()" in contents
