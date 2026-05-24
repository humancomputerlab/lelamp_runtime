import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class PackagingMetadataTests(unittest.TestCase):
    def test_pyproject_declares_explicit_build_backend(self) -> None:
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(data["build-system"]["build-backend"], "setuptools.build_meta")
        self.assertIn("setuptools", "".join(data["build-system"]["requires"]))

    def test_pyproject_limits_package_discovery_to_lelamp(self) -> None:
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        package_find = data["tool"]["setuptools"]["packages"]["find"]
        self.assertEqual(package_find["include"], ["lelamp*"])


if __name__ == "__main__":
    unittest.main()
