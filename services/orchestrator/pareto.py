"""All distinct cost/time/reliability tradeoffs within the authorized budget."""

from fractions import Fraction
from itertools import product
from math import prod


def percent(value):
    cents = (value.numerator * 10000 * 2 + value.denominator) // (2 * value.denominator)
    return f"{cents // 100}.{cents % 100:02d}"


def team_key(option):
    return tuple(offer["agentId"] for offer in option["team"])


def elapsed_seconds(tasks, team):
    """Reference schedule: dependencies plus one active task per merchant wallet.

    Ready nodes may start without waiting for an unrelated slow branch. This is
    a deterministic estimate, not a promise about the live dispatcher's choices.
    """
    pending = list(zip(tasks, team, strict=True))
    running, done, now = {}, set(), 0
    while pending or running:
        occupied = {wallet for _, wallet in running.values()}
        for spec, offer in list(pending):
            wallet = offer.get("wallet", offer["agentId"])
            if set(spec.dependencies) <= done and wallet not in occupied:
                running[spec.key] = (now + offer["seconds"], wallet)
                occupied.add(wallet)
                pending.remove((spec, offer))
        if not running:
            raise ValueError("任务依赖无法执行")
        now = min(end for end, _ in running.values())
        for key, (end, _) in list(running.items()):
            if end == now:
                done.add(key)
                del running[key]
    return now


def frontier(tasks, pools, budget, preference="lowest_price"):
    if any(not pool for pool in pools):
        raise ValueError("有子任务没有可用商家")
    use_history = all(o["history"]["settled"] + o["history"]["expired"] > 0 for pool in pools for o in pool)
    score = lambda o: o["success"] if use_history else Fraction(0)
    combinations = prod(len(pool) for pool in pools)
    if combinations > 100_000:
        raise ValueError("候选组合超过 100000，无法完成精确前沿计算，请缩小候选范围")
    feasible = []
    for team in product(*pools):
        total = sum(offer["price"] for offer in team)
        if total > budget:
            continue
        success = Fraction(1)
        for spec, offer in zip(tasks, team, strict=True):
            history = offer["history"]
            count = history["settled"] + history["expired"]
            if count <= 0:
                success = None
                break
            success *= Fraction(history["settled"], count)
        feasible.append({"team": team, "price": total, "seconds": elapsed_seconds(tasks, team), "success": success})
    if not feasible:
        minimum = sum(min(o["price"] for o in pool) for pool in pools)
        raise ValueError(f"全部组合均超过预算；最低服务费为 {minimum / 1_000_000:.2f} USDC")
    feasible.sort(key=lambda item: (item["price"], item["seconds"], -score(item), team_key(item)))
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
        if best < score(option):
            nondominated.append(option)
        index = position
        while index < len(tree):
            tree[index] = max(tree[index], score(option))
            index += index & -index
    options = nondominated
    cheapest = min(o["price"] for o in options)
    fastest = min(o["seconds"] for o in options)
    most_reliable = max(score(o) for o in options)
    for option in options:
        option["advantages"] = []
        if option["price"] == cheapest:
            option["advantages"].append("费用最低")
        if option["seconds"] == fastest:
            option["advantages"].append("预计最快")
        if use_history and option["success"] == most_reliable:
            option["advantages"].append("参考成功率最高")
    orders = {"lowest_price": lambda o: (o["price"], o["seconds"], -score(o), team_key(o)),
              "fastest": lambda o: (o["seconds"], o["price"], -score(o), team_key(o)),
              "reliability": lambda o: (-score(o), o["price"], o["seconds"], team_key(o))}
    options.sort(key=orders[preference])
    return {"options": options, "candidateCombinations": combinations, "feasibleCount": len(feasible),
            "frontierCount": len(nondominated), "displayLimit": None, "truncated": False,
            "objectives": ["price", "seconds", *(["success"] if use_history else [])],
            "comparisonNotice": "" if use_history else "部分商家尚无履约记录，本轮按费用与耗时比较；不把没有记录当作失败。"}


def public(option):
    probability = option["success"]
    if probability is None:
        return {"estimatedSuccessPercent": None, "successNumerator": None, "successDenominator": None,
                "advantages": option.get("advantages", [])}
    return {"estimatedSuccessPercent": percent(probability), "successNumerator": str(probability.numerator),
            "successDenominator": str(probability.denominator), "advantages": option.get("advantages", [])}
