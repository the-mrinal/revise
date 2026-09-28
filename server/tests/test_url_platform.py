from main import detect_platform, normalize_url


def test_normalize_strips_query_and_fragment():
    assert (
        normalize_url("https://codeforces.com/problemset/problem/1/A?locale=en#hint")
        == "https://codeforces.com/problemset/problem/1/A/"
    )


def test_normalize_leetcode_collapses_subpaths():
    assert (
        normalize_url("https://leetcode.com/problems/two-sum/description/?envType=daily")
        == "https://leetcode.com/problems/two-sum/"
    )


def test_normalize_leetcode_keeps_non_problem_paths():
    assert normalize_url("https://leetcode.com/problemset/") == "https://leetcode.com/problemset/"


def test_detect_known_platforms():
    assert detect_platform("https://leetcode.com/problems/two-sum/") == "leetcode"
    assert detect_platform("https://ATCODER.jp/contests/abc001") == "atcoder"
    assert detect_platform("https://algo.monster/problems/two_sum") == "algomonster"


def test_detect_unknown_platform_falls_back_to_other():
    assert detect_platform("https://example.com/quiz/1") == "other"


def test_user_platforms_take_precedence():
    user_platforms = [{"name": "myjudge", "url_pattern": r"leetcode\.com"}]
    assert detect_platform("https://leetcode.com/problems/two-sum/", user_platforms) == "myjudge"


def test_normalize_cses_task_tabs_are_the_task():
    # Sunday bug report #25: a save from the Submit page kept "submit/1068".
    for page in ("task", "submit", "stats", "hack"):
        assert (
            normalize_url(f"https://cses.fi/problemset/{page}/1068/")
            == "https://cses.fi/problemset/task/1068/"
        )
    assert normalize_url("https://cses.fi/problemset/task/1068") == "https://cses.fi/problemset/task/1068/"


def test_normalize_cses_result_is_left_alone():
    # A result page's number is a submission, not a task.
    assert (
        normalize_url("https://cses.fi/problemset/result/1234567/")
        == "https://cses.fi/problemset/result/1234567/"
    )


def test_normalize_codeforces_contest_is_the_problemset_problem():
    assert (
        normalize_url("https://codeforces.com/contest/1/problem/a?locale=en")
        == "https://codeforces.com/problemset/problem/1/A/"
    )
    assert (
        normalize_url("https://codeforces.com/problemset/problem/1/A")
        == "https://codeforces.com/problemset/problem/1/A/"
    )
