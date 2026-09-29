"""Unit tests for the .spy import hook system."""

import sys
from pathlib import Path
from pycli.importer import install_import_hook, uninstall_import_hook


def test_import_spy_module(tmp_path: Path):
    """Test importing a single .spy module from disk."""
    install_import_hook()

    # Create a dummy .spy module
    module_code = '''def greet(name: str) -> str:
    msg = $(echo Hello {name})
    return msg.stdout.strip()

CONSTANT = 42
'''
    spy_file = tmp_path / "my_helper.spy"
    spy_file.write_text(module_code, encoding="utf-8")

    # Add tmp_path to sys.path
    sys.path.insert(0, str(tmp_path))

    try:
        import my_helper  # type: ignore

        assert hasattr(my_helper, "greet")
        assert my_helper.greet("DevOps") == "Hello DevOps"
        assert my_helper.CONSTANT == 42
    finally:
        sys.path.remove(str(tmp_path))
        if "my_helper" in sys.modules:
            del sys.modules["my_helper"]


def test_import_spy_package(tmp_path: Path):
    """Test importing a package structured with __init__.spy and submodules."""
    install_import_hook()

    pkg_dir = tmp_path / "devops_pkg"
    pkg_dir.mkdir()

    init_code = '''from devops_pkg.git_ops import get_branch

PACKAGE_NAME = "devops_pkg"
'''
    (pkg_dir / "__init__.spy").write_text(init_code, encoding="utf-8")

    submodule_code = '''def get_branch() -> str:
    return "main"
'''
    (pkg_dir / "git_ops.spy").write_text(submodule_code, encoding="utf-8")

    sys.path.insert(0, str(tmp_path))

    try:
        import devops_pkg

        assert devops_pkg.PACKAGE_NAME == "devops_pkg"
        assert devops_pkg.get_branch() == "main"
    finally:
        sys.path.remove(str(tmp_path))
        for mod in list(sys.modules.keys()):
            if mod.startswith("devops_pkg"):
                del sys.modules[mod]


def test_spy_imports_spy_file_execution(tmp_path: Path):
    """Test that run_file executes a .spy script that imports a sibling .spy file."""
    from pycli import run_file

    lib_code = '''def compute_version(prefix: str) -> str:
    res = $(echo {prefix}-1.0.0)
    return res.stdout.strip()
'''
    (tmp_path / "version_lib.spy").write_text(lib_code, encoding="utf-8")

    main_code = '''import version_lib

ver = version_lib.compute_version("v")
assert ver == "v-1.0.0"
'''
    main_file = tmp_path / "main.spy"
    main_file.write_text(main_code, encoding="utf-8")

    exit_code = run_file(main_file)
    assert exit_code == 0


def test_uninstall_import_hook():
    """Test installing and uninstalling the import hook."""
    finder = install_import_hook()
    assert finder in sys.meta_path

    uninstall_import_hook()
    assert finder not in sys.meta_path

    # Reinstall for other tests
    install_import_hook()


def test_importer_cache_with_options_int01(tmp_path: Path):
    from pycli.importer import _get_cached_code

    spy_file = tmp_path / "mod.spy"
    source = "def run_cmd():\n    return $(echo hi).text\n"
    spy_file.write_text(source, encoding="utf-8")

    # Get cached code with default options
    code1 = _get_cached_code(spy_file, source, unsafe_interpolation=False)
    # Get cached code with unsafe_interpolation=True
    code2 = _get_cached_code(spy_file, source, unsafe_interpolation=True)

    cache_dir = tmp_path / "__pycache__"
    pyc_files = list(cache_dir.glob("*.pyc"))
    # Two distinct pyc files should be generated because options differ
    assert len(pyc_files) == 2
