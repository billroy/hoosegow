import pytest

import toady


def test_toady_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        toady.parse_args(["--version"])

    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"toady {toady.__version__}"
