"""Exact cost/time/reliability frontier, followed by bounded representative display."""

from fractions import Fraction
from itertools import product
from math import prod


def percent(value):
    cents = (value.numerator * 10000 * 2 + value.denominator) // (2 * value.denominator)
    return f"{cents // 100}.{cents % 100:02d}"


def team_key(option):
    return tuple(offer["agentId"] for offer in option["team"])


def frontier(tasks, pools, budget, preference="lowest_price", limit=10):
    if any(not pool for pool in pools):
        raise ValueError("有子任务没有可用且具备近期履约记录的商家，无法比较三个指标")
    combinations = prod(len(pool) for pool in pools)
    if combinations > 100_000:
        raise ValueError("候选组合超过 100000，无法完成精确前沿计算，请缩小候选范围")
    feasible = []
    for team in product(*pools):
        total = sum(offer["price"] for offer in team)
        if total > budget:
            continue
        finishes, success = {}, Fraction(1)
        for spec, offer in zip(tasks, team, strict=True):
            finishes[spec.key] = offer["seconds"] + max((finishes[key] for key in spec.dependencies), default=0)
            history = offer["history"]
            count = history["settled"] + history["expired"]
            if count <= 0:
                raise ValueError("缺少近期履约记录，不能虚构任务成功率")
            success *= Fraction(history["settled"], count)
        feasible.append({"team": team, "price": total, "seconds": max(finishes.values(), default=0), "success": success})
    if not feasible:
        minimum = sum(min(o["price"] for o in pool) for pool in pools)
        raise ValueError(f"全部组合均超过预算；最低服务费为 {minimum / 1_000_000:.2f} USDC")
    feasible.sort(key=lambda item: (item["price"], item["seconds"], -item["success"], team_key(item)))
    # Price sweep + prefix maximum over elapsed time: exact rational comparisons, O(N log N).
    times = {value: index + 1 for index, value in enumerate(sorted({f["seconds"] for f in feasible}))}
    tree = [Fraction(-1)] * (len(times) + 1)
    nondominated = []
    for option in feasible:
        position = times[option["seconds"]]
        index, best = position, Fraction(-1)
        while index:
            best = max(best, tree[index])
            index -= index & -index
        if best < option["success"]:
            nondominated.append(option)
        index = position
        while index < len(tree):
            tree[index] = max(tree[index], option["success"])
            index += index & -index
    selected = list(range(len(nondominated)))
    if len(selected) > limit:
        # Keep all objective extremes, then maximize separation for readable coverage of the frontier.
        selected = list(dict.fromkeys([
            min(range(len(nondominated)), key=lambda i: (nondominated[i]["price"], nondominated[i]["seconds"], -nondominated[i]["success"])),
            min(range(len(nondominated)), key=lambda i: (nondominated[i]["seconds"], nondominated[i]["price"], -nondominated[i]["success"])),
            min(range(len(nondominated)), key=lambda i: (-nondominated[i]["success"], nondominated[i]["price"], nondominated[i]["seconds"])),
        ]))
        coords = [(o["price"], o["seconds"], float(o["success"])) for o in nondominated]
        lows = [min(c[d] for c in coords) for d in range(3)]
        spans = [max(c[d] for c in coords) - lows[d] or 1 for d in range(3)]
        coords = [tuple((c[d] - lows[d]) / spans[d] for d in range(3)) for c in coords]
        while len(selected) < limit:
            chosen = set(selected)
            selected.append(max((i for i in range(len(coords)) if i not in chosen), key=lambda i: (
                min(sum(abs(coords[i][d] - coords[j][d]) for d in range(3)) for j in selected), -i)))
    options = [nondominated[i] for i in selected]
    orders = {"lowest_price": lambda o: (o["price"], o["seconds"], -o["success"], team_key(o)),
              "fastest": lambda o: (o["seconds"], o["price"], -o["success"], team_key(o)),
              "reliability": lambda o: (-o["success"], o["price"], o["seconds"], team_key(o))}
    options.sort(key=orders[preference])
    return {"options": options, "candidateCombinations": combinations, "feasibleCount": len(feasible),
            "frontierCount": len(nondominated), "displayLimit": limit, "truncated": len(nondominated) > limit}


def public(option):
    probability = option["success"]
    return {"estimatedSuccessPercent": percent(probability), "successNumerator": str(probability.numerator),
            "successDenominator": str(probability.denominator)}
