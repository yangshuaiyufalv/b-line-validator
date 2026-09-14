#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
b_line_stats.py —— B 线状态基准（最新版感知）

输出即「唯一状态基准」：现行版本与条数、手法/资产分布、缺口与提醒、校验状态、索引一致性。
自动化任务 `B线-融资租赁新案发现` 第一步即运行本脚本，报告须以脚本输出为准。

⚠ 禁止硬编码任何版本号 / 条数 / 分布数字 —— 全部由数据实时计算。
本版本在旧版基础上新增：C1/C2/C4 完整性分布、court_level 待回填计数、
A1 案号年份≠judgment_year 计数、低频手法(<5)与最少资产类型定向优先级、校验状态（调用 validate_line_b）。
"""
import sys, json, re, os, collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import validate_line_b as V


def load(path):
    recs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    return recs


def _resolve_path():
    if len(sys.argv) > 1:
        return sys.argv[1]
    cands = []
    for d in [".", "data/output", "../data/output", "../../data/output"]:
        if os.path.isdir(d):
            for fn in os.listdir(d):
                if re.match(r"融资租赁合同诈骗案例特征库_v.*\.jsonl\.txt$", fn):
                    cands.append(os.path.join(d, fn))
    if not cands:
        return None
    return sorted(cands)[-1]


def dist(recs, field):
    return dict(collections.Counter(r.get(field) for r in recs))


def main():
    path = _resolve_path()
    if not path or not os.path.isfile(path):
        print("未找到数据集文件（可传参：python3 b_line_stats.py <path>）")
        sys.exit(2)
    recs = load(path)
    fname = os.path.basename(path)
    m = re.search(r"v(\d+(?:\.\d+)*)", fname)
    version = m.group(1) if m else "未知"
    if version != V.VALIDATOR_VERSION:
        print(f"⚠ 校验器版本(v{V.VALIDATOR_VERSION}) 与数据集版本(v{version}) 不一致："
              f"可能缺新字段/新规则，请主线更新 scripts/ 内嵌校验器（见 prompt 第零步）")
    n = len(recs)
    fields = set()
    for r in recs:
        fields.update(r.keys())

    print("# B 线状态基准（自动生成，禁止硬编码）")
    print(f"数据集文件 : {fname}")
    print(f"现行版本   : v{version}")
    print(f"记录数     : {n}")
    print(f"字段数     : {len(fields)}")
    print(f"字段一致性 : {'一致' if all(set(r.keys()) == fields for r in recs) else '不一致'}")

    print("\n## 手法分布 (crime_pattern)")
    for k, v in sorted(dist(recs, "crime_pattern").items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")

    print("\n## 资产类型分布 (asset_type)")
    for k, v in sorted(dist(recs, "asset_type").items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")

    print("\n## 法院层级 (court_level)")
    for k, v in sorted(dist(recs, "court_level").items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")

    print("\n## 来源类型 (source_type)")
    for k, v in sorted(dist(recs, "source_type").items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")

    print("\n## 完整性缺口 (C1/C2/C4)")
    print(f"  C1 case_number_status : {dist(recs,'case_number_status')}")
    print(f"  C2 amount_disclosure  : {dist(recs,'amount_disclosure')}")
    print(f"  C4 source_url_status  : {dist(recs,'source_url_status')}")

    print("\n## 缺口与提醒")
    cl_none = [r.get("case_id") for r in recs if not r.get("court_level")]
    print(f"  court_level 待回填(None) : {len(cl_none)}")
    if cl_none:
        print(f"    -> {cl_none}")

    ann_missing = [r.get("case_id") for r in recs if not r.get("annotator")]
    print(f"  annotator 缺失          : {len(ann_missing)}")

    # A1 案号年份≠judgment_year
    a1 = []
    for r in recs:
        stages = V.parse_stages(r.get("case_number", ""))
        jy = r.get("judgment_year")
        if stages and jy is not None:
            ctrl = None
            for y, st in stages:
                if st == "终":
                    ctrl = y
            if ctrl is None:
                ctrl = stages[0][0]
            if int(ctrl) != int(jy):
                a1.append((r.get("case_id"), ctrl, jy))
    print(f"  A1 案号控制年份≠judgment_year(须三源核验) : {len(a1)}")
    for cid, c, j in a1[:15]:
        print(f"    - {cid}: 控制年{c} / judgment_year{j}")

    # 低频手法 <5
    low = [(k, v) for k, v in dist(recs, "crime_pattern").items() if v < 5]
    print(f"  低频手法(<5条, 检索优先级高) : {sorted(low, key=lambda x:x[1])}")

    # 最少资产类型
    at = sorted(dist(recs, "asset_type").items(), key=lambda x: x[1])
    print(f"  最少资产类型(定向检索优先级) : {at[:3]}")

    print("\n## 校验状态")
    viol = V.validate(recs)
    hard = [v for v in viol if v[0] == "HARD"]
    warn = [v for v in viol if v[0] == "WARN"]
    if hard:
        bt = collections.Counter(code for sev, code, cid, msg in hard)
        print(f"  校验违规: {len(hard)} 条 -> {dict(bt)}")
    else:
        print(f"  PASS（无 HARD 违规；WARN={len(warn)}）")

    print("\n## 索引一致性")
    ids = [r.get("case_id") for r in recs]
    dup = [k for k, v in collections.Counter(ids).items() if v > 1]
    print(f"  case_id 唯一 : {'是' if not dup else '否 重复='+str(dup)}")
    print(f"  case_id 数量 : {len(ids)} / 唯一值 : {len(set(ids))}")


if __name__ == "__main__":
    main()
