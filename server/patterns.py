"""DSA Patterns data from Swati's LeetCode Patterns Sheet (94 patterns, 15 categories)."""

from __future__ import annotations

import json
import os
import re

# {category: {pattern_name: [problem_numbers]}}
PATTERNS = {
    "Two Pointers": {
        "Converging": [11, 15, 16, 18, 167, 349, 881, 977, 259],
        "Fast & Slow": [141, 202, 287, 392],
        "Fixed Separation": [19, 876, 2095],
        "In-place Array Modification": [26, 27, 75, 80, 283, 443, 905, 2337, 2938],
        "String Comparison": [844, 1598, 2390],
        "Expanding From Center": [5, 647],
        "String Reversal": [151, 344, 345, 541],
    },
    "Sliding Window": {
        "Fixed Size": [346, 643, 2985, 3254, 3318],
        "Variable Size": [3, 76, 209, 219, 424, 713, 904, 1004, 1438, 1493, 1658, 1838, 2461, 2516, 2762, 2779, 2981, 3026, 3346, 3347],
        "Monotonic Queue": [239, 862, 1696],
        "Character Frequency Matching": [1, 438, 567],
    },
    "Tree Traversal": {
        "Level Order Traversal": [102, 103, 199, 515, 1161],
        "Recursive Preorder": [100, 101, 105, 114, 226, 257, 988],
        "Recursive Inorder": [94, 98, 173, 230, 501, 530],
        "Recursive Postorder": [104, 110, 124, 145, 337, 366, 543, 863, 1110, 2458],
        "Lowest Common Ancestor": [235, 236],
        "Serialization/Deserialization": [297, 572, 652],
    },
    "Graph Traversal": {
        "DFS Connected Components": [130, 200, 417, 547, 695, 733, 841, 1020, 1254, 1905, 2101],
        "BFS Connected Components": [542, 994, 1091],
        "DFS Cycle Detection": [207, 210, 802, 1059],
        "BFS Topological Sort": [210, 269, 310, 444, 1136, 1857, 2050, 2115, 2392],
        "Deep Copy/Cloning": [133, 138, 1334, 1490],
        "Shortest Path (Dijkstra)": [743, 778, 1514, 1631, 1976, 2045, 2203, 2290, 2577, 2812],
        "Shortest Path (Bellman-Ford)": [787, 1129],
        "Union-Find": [200, 261, 305, 323, 547, 684, 721, 737, 947, 952, 959, 1101],
        "Strongly Connected Components": [210, 547, 1192, 2127],
        "Bridges & Articulation Points": [1192, 2360],
        "Minimum Spanning Tree": [1135, 1168, 1489, 1584],
        "Bidirectional BFS": [126, 127, 815],
    },
    "Dynamic Programming": {
        "Fibonacci Style": [70, 91, 198, 213, 337, 509, 740, 746],
        "Kadane's Algorithm": [53, 152, 918, 1749, 2321],
        "Coin Change": [322, 377, 518],
        "0/1 Knapsack": [416, 494],
        "Word Break Style": [139, 140],
        "Longest Common Subsequence": [1092, 1143, 1312],
        "Edit Distance": [72, 583, 712],
        "Unique Paths on Grid": [62, 63, 64, 120, 221, 931, 1277],
        "Interval DP": [312, 546],
        "Catalan Numbers": [95, 96, 241],
        "Longest Increasing Subsequence": [300, 354, 1671, 2407],
        "Stock Problems": [121, 122, 123, 188, 309],
    },
    "Heap (Priority Queue)": {
        "Top K Elements": [215, 347, 451, 506, 703, 973, 1046, 2558],
        "Two Heaps": [295, 1825],
        "K-way Merge": [23, 373, 378, 632],
        "Scheduling/Minimum Cost": [253, 767, 857, 1642, 1792, 1834, 1942, 2402],
    },
    "Backtracking": {
        "Subsets": [17, 77, 78, 90],
        "Permutations": [31, 46, 60],
        "Combination Sum": [39, 40],
        "Parentheses Generation": [22, 301],
        "Word Search": [79, 212, 2018],
        "N-Queens": [37, 51],
        "Palindrome Partitioning": [131, 132, 1457],
    },
    "Greedy": {
        "Interval Merging": [56, 57, 759, 986, 2406],
        "Jump Game": [45, 55],
        "Buy/Sell Stock": [121, 122],
        "Gas Station": [134, 2202],
        "Task Scheduling": [621, 767, 1054],
        "Sorting Based": [135, 406, 455, 1029],
    },
    "Binary Search": {
        "On Sorted Array": [35, 69, 74, 278, 374, 540, 704, 1539],
        "Rotated Sorted Array": [33, 81, 153, 162, 852, 1095],
        "On Answer (Binary Search on Result)": [410, 774, 875, 1011, 1482, 1760, 2064, 2226],
        "First/Last Occurrence": [34, 658],
        "Median/Kth": [4, 378, 719],
    },
    "Stack": {
        "Valid Parentheses": [20, 32, 921, 1249, 1963],
        "Monotonic Stack": [402, 496, 503, 739, 901, 907, 962, 1475, 1673],
        "Expression Evaluation": [150, 224, 227, 772],
        "Simulation": [71, 394, 735],
        "Min Stack Design": [155, 895, 901],
        "Largest Rectangle": [84, 85],
    },
    "Bit Manipulation": {
        "Bitwise XOR": [136, 137, 268, 389],
        "Bitwise AND": [191, 231, 477],
        "Bitwise DP": [338, 1442, 1494],
        "Power Check": [231, 342],
    },
    "Linked List": {
        "In-place Reversal": [25, 82, 83, 92, 206, 234],
        "Merging Sorted Lists": [21, 23],
        "Number Addition": [2, 369],
        "Intersection Detection": [160, 599],
        "Reordering": [24, 61, 86, 143, 328],
    },
    "Array/Matrix": {
        "In-place Rotation": [48, 189, 867],
        "Spiral Traversal": [54, 59, 885, 2326],
        "In-place Marking": [73, 289, 498],
        "Prefix/Suffix Products": [238, 845, 2483],
        "Plus One / Manual Arithmetic": [43, 66, 67, 989],
        "In-place from End": [88, 977],
        "Cyclic Sort": [41, 268, 287, 442, 448],
    },
    "String Manipulation": {
        "Palindrome Check": [9, 125, 680],
        "Anagram Check": [49, 242],
        "Roman Conversion": [12, 13],
        "String to Integer": [8, 65],
        "Manual Simulation": [43, 67, 415],
        "String Matching": [28, 214, 686, 796, 3008],
        "Repeated Substring": [28, 459, 686],
    },
    "Design": {
        "General Design": [146, 155, 225, 232, 251, 271, 295, 341, 346, 353, 359, 362, 379, 380, 432, 460, 604, 622, 641, 642, 706, 715, 900, 981, 1146, 1348, 1352, 1381, 1756, 2013, 2034, 2296, 2336],
        "Tries": [208, 211, 425, 648, 720, 745],
    },
}

# LeetCode problems by number: slug, official title and difficulty.
# research.html reads the same file (embedded by the /research route), so the
# two stay the same. Refresh it with scripts/fetch_problem_titles.py.
PROBLEMS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "problems.json")
with open(PROBLEMS_FILE, encoding="utf-8") as _f:
    PROBLEMS_DATA = json.load(_f)

# {problem_number: {"slug", "title", "difficulty"}}
PROBLEMS: dict[int, dict] = {int(k): v for k, v in PROBLEMS_DATA["problems"].items()}

# LeetCode problem number → slug mapping (for URL generation)
PROBLEM_SLUGS: dict[int, str] = {num: p["slug"] for num, p in PROBLEMS.items()}

# Build reverse lookup: problem_number → (category, pattern_name)
# A problem may appear in multiple patterns; we store the first match.
PROBLEM_TO_PATTERN: dict[int, tuple[str, str]] = {}
for _cat, _patterns in PATTERNS.items():
    for _pat, _nums in _patterns.items():
        for _num in _nums:
            if _num not in PROBLEM_TO_PATTERN:
                PROBLEM_TO_PATTERN[_num] = (_cat, _pat)


def extract_leetcode_number(url: str) -> int | None:
    """Extract LeetCode problem number from a URL by matching the slug."""
    m = re.search(r"leetcode\.com/problems/([^/]+)", url)
    if not m:
        return None
    slug = m.group(1).lower()
    for num, s in PROBLEM_SLUGS.items():
        if s == slug:
            return num
    return None


def get_pattern_for_problem(problem_number: int) -> dict | None:
    """Return pattern info for a problem number, or None."""
    entry = PROBLEM_TO_PATTERN.get(problem_number)
    if not entry:
        return None
    return {"category": entry[0], "pattern": entry[1], "label": f"{entry[0]} > {entry[1]}"}


def get_pattern_for_url(url: str) -> str | None:
    """Return pattern label string for a LeetCode URL, or None."""
    num = extract_leetcode_number(url)
    if num is None:
        return None
    info = get_pattern_for_problem(num)
    return info["label"] if info else None


def get_all_pattern_labels() -> list[str]:
    """Return flat list of all pattern labels for dropdown menus."""
    labels = []
    for cat, patterns in PATTERNS.items():
        for pat in patterns:
            labels.append(f"{cat} > {pat}")
    return labels


def get_all_patterns() -> dict:
    """Return the full patterns tree."""
    return PATTERNS


def get_bank() -> dict:
    """The sheet as groups → patterns → problems, with titles and difficulties."""
    groups = []
    for cat, patterns in PATTERNS.items():
        groups.append({
            "name": cat,
            "patterns": [
                {
                    "name": pat,
                    "problems": [
                        {
                            "number": num,
                            "title": PROBLEMS[num]["title"],
                            "slug": PROBLEMS[num]["slug"],
                            "difficulty": PROBLEMS[num]["difficulty"],
                        }
                        for num in nums
                    ],
                }
                for pat, nums in patterns.items()
            ],
        })
    return {"groups": groups}
