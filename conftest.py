"""pytest: добавляем корень репозитория в sys.path, чтобы тесты видели `lib`/`gsa_checker`."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
