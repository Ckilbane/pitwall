"""WP2: priors (SPEC 2.6): archive file/dir support, 2022 uses 2020 then 2019, offseason excluded, chain."""
import csv
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from sb import priors  # noqa: E402

ARCHIVE = os.path.join(os.path.dirname(HERE), "priors", "archive_team_years_2016_2023.csv")


def _rows():
    with open(ARCHIVE, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_prior_years_skip_2021():
    assert priors.prior_years(2022) == [2020, 2019, 2018]
    assert priors.prior_years(2023) == [2022, 2020, 2019]
    assert priors.prior_years(2024) == [2023, 2022, 2020]
    assert priors.prior_years(2026) == [2025, 2024, 2023, 2022]


def test_archive_file_and_directory_agree(tmp_path):
    d = tmp_path / "arch"
    d.mkdir()
    (d / "team_years.csv").write_bytes(open(ARCHIVE, "rb").read())
    a = priors.load_archive_years(ARCHIVE, [2019, 2020])
    b = priors.load_archive_years(str(d), [2019, 2020])
    assert {y: {t: v.norm_epa for t, v in m.items()} for y, m in a.items()} == \
           {y: {t: v.norm_epa for t, v in m.items()} for y, m in b.items()}
    assert priors.archive_csv_path(ARCHIVE) == ARCHIVE
    assert priors.archive_csv_path(str(d)).endswith("team_years.csv")


def test_2022_priors_come_from_2020_and_2019_without_offseason():
    pri, src = priors.build_priors(2022, ARCHIVE, None, ())
    assert set(pri) == {2018, 2019, 2020} and src[2020] == "archive" and src[2019] == "archive"
    rows = _rows()
    off = {int(r["team"]) for r in rows if r["year"] == "2020" and r["offseason"] == "t" and r["team"].isdigit()}
    on = {int(r["team"]) for r in rows if r["year"] == "2020" and r["offseason"] == "f" and r["team"].isdigit()}
    assert on and set(pri[2020]) == on
    assert off - on == (off & set(pri[2020])) - on or not (off - on) & set(pri[2020])
    assert not ((off - on) & set(pri[2020]))          # offseason-only teams are excluded
    with_off, _ = priors.build_priors(2022, ARCHIVE, None, (), include_offseason=True)
    assert len(with_off[2020]) >= len(pri[2020])


def test_non_numeric_team_ids_skipped():
    rows = _rows()
    nonnum = [r for r in rows if not r["team"].isdigit()]
    pri = priors.load_archive_years(ARCHIVE, [int(nonnum[0]["year"])]) if nonnum else None
    if nonnum:
        assert all(isinstance(t, int) for t in pri[int(nonnum[0]["year"])])


def test_chain_prefers_computed_season(tmp_path):
    d = tmp_path / "out" / "2022"
    d.mkdir(parents=True)
    (d / "team_years.csv").write_text("team,norm_epa\n1,1700\n2,\n", encoding="utf-8")
    pri, src = priors.build_priors(2023, ARCHIVE, str(tmp_path / "out"), computed={2022})
    assert src == {2019: "archive", 2020: "archive", 2022: "computed"}
    assert pri[2022][1].norm_epa == 1700 and isinstance(pri[2022][1].norm_epa, int)
    assert pri[2022][2].norm_epa is None
    pri2, src2 = priors.build_priors(2023, ARCHIVE, str(tmp_path / "out"), computed=())
    assert src2[2022] == "archive" and len(pri2[2022]) > 1000


def test_missing_everywhere_is_absent():
    pri, src = priors.build_priors(2026, ARCHIVE, None, ())
    assert src[2025] == "missing" and 2025 not in pri and src[2023] == "archive"
