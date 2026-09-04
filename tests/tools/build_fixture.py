"""Build a .dacpac fixture from a directory of .sql files.

Usage:
    uv run python tests/tools/build_fixture.py \
        --src tests/fixtures/sql \
        --out tests/fixtures/dacpacs/sample.dacpac \
        --name sample

Uses DacFx via pythonnet (no sqlpackage required). Requires the CLR bootstrap to
resolve to a working DacFx runtime (see `dacpactools tools install-dacfx`).
"""
from __future__ import annotations

import argparse
from pathlib import Path

from dacpactools.infrastructure.dotnet.clr_bootstrap import initialize
from dacpactools.presentation.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, required=True, help="Directory of *.sql files.")
    parser.add_argument("--out", type=Path, required=True, help="Target .dacpac path.")
    parser.add_argument("--name", default="fixture", help="Package Name metadata.")
    parser.add_argument("--version", default="1.0.0", help="Package Version metadata.")
    args = parser.parse_args()

    initialize(Settings())  # loads DacFx into the process
    from Microsoft.SqlServer.Dac import DacPackageExtensions, PackageMetadata  # type: ignore
    from Microsoft.SqlServer.Dac.Model import (  # type: ignore
        SqlServerVersion,
        TSqlModel,
        TSqlModelOptions,
    )

    model = TSqlModel(SqlServerVersion.SqlAzure, TSqlModelOptions())
    try:
        sql_files = sorted(args.src.rglob("*.sql"))
        if not sql_files:
            raise SystemExit(f"no .sql files under {args.src}")
        for f in sql_files:
            text = f.read_text(encoding="utf-8")
            for stmt in [s.strip() for s in text.split("\n\n") if s.strip()]:
                model.AddObjects(stmt)
        metadata = PackageMetadata()
        metadata.Name = args.name
        metadata.Version = args.version
        args.out.parent.mkdir(parents=True, exist_ok=True)
        DacPackageExtensions.BuildPackage(str(args.out), model, metadata)
        print(f"built {args.out}")
    finally:
        model.Dispose()


if __name__ == "__main__":
    main()
