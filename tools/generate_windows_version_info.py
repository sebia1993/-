from __future__ import annotations

import argparse
import re
from pathlib import Path


VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-rc\.(\d+))?$")


def build_version_info(version: str, *, product_name: str, description: str, filename: str) -> str:
    match = VERSION_RE.fullmatch(version)
    if not match:
        raise ValueError("version must use vMAJOR.MINOR.PATCH or vMAJOR.MINOR.PATCH-rc.N")
    numbers = tuple(int(value or 0) for value in match.groups())
    numeric = ", ".join(str(value) for value in numbers)
    return f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({numeric}),
    prodvers=({numeric}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('FileDescription', '{description}'),
          StringStruct('FileVersion', '{version}'),
          StringStruct('InternalName', '{Path(filename).stem}'),
          StringStruct('LegalCopyright', 'Internal use only'),
          StringStruct('OriginalFilename', '{filename}'),
          StringStruct('ProductName', '{product_name}'),
          StringStruct('ProductVersion', '{version}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def write_text_lf(path: Path, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--product-name", required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--filename", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(
        output,
        build_version_info(
            args.version,
            product_name=args.product_name,
            description=args.description,
            filename=args.filename,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
