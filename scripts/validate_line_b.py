#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_line_b.py —— B 线（融资租赁合同诈骗案例）schema + 一致性校验器（最新版感知）

用途：候选案例字段必须通过本校验器的约束，方可并入「融资租赁合同诈骗案例特征库」。
自动化任务 `B线-融资租赁新案发现` 在每次扫描时应先运行本校验器，
若输出「校验违规」（退出码 1）则必须在报告中先注明数据侧问题再继续。

本版本对应「今日修改事项」新增/强化的约束：
  * 46 字段 schema 强制（防缺失 / 多余字段）
  * C 系列完整性：C1 case_number_status / C2 amount_disclosure / C4 source_url_status 枚举域 + annotator 必填
  * B 系列枚举：restitution_std、exit_mode（token 级）、asset_authenticity（token 级）
  * A 系列逻辑一致性：
      A1 案号控制年份 ↔ judgment_year（FL-2026-119 教训：任何不一致须三源核验）
      A2 prison_term 禁 0 占位（非无罪案）
      A3 conviction 仅存纯罪名（不得混入刑期/缓刑字样）
      A4 退赔标准 ↔ 退赔金额一致性

退出码：发现 HARD 违规 = 1；仅 WARN = 0；通过 = 0；未找到文件 = 2
"""
import sys, json, re, os

# 校验器逻辑对应的特征库 schema 版本；与数据集文件名 vX.Y 不一致时，
# b_line_stats 会在报告中提醒主线更新本内嵌校验器（防止版本演进后漏新字段/新规则）。
VALIDATOR_VERSION = "3.0"

# 46 字段 schema（合法字段全集；缺失/多余均算 SCHEMA 违规）
REQUIRED_FIELDS = [
    "actual_loss","amount_disclosure","annotation_date","annotator","asset_authenticity",
    "asset_type","case_id","case_number","case_number_status","charge","charge_secondary",
    "civil_disposition","conviction","court","court_level","crime_flow","crime_pattern",
    "crime_pattern_detail","duration","exit_mode","fine_amount","fund_destination",
    "involves_blacklist_person","involves_insider","is_civil_criminal","is_organized",
    "judgment_year","lease_mode","main_perpetrator_identity","main_perpetrator_role",
    "org_structure","participant_structure","prison_term","restitution","restitution_std",
    "sentencing_factors","source_title","source_type","source_url","source_url_status",
    "suspended","total_amount","valuation_method","victim_count","victim_detail","victim_type",
]

# HARD 枚举域（不在域内即阻断并入）
ALLOWED = {
    "case_number_status": {"脱敏","完整","缺失"},                       # C1
    "amount_disclosure": {"已披露","未披露","部分披露"},                 # C2
    "source_url_status": {"已附URL","仅首页","未获取"},                 # C4
    "court_level": {None,"基层法院","中级法院","高级法院"},
    "restitution_std": {"全额退赔","部分退赔","未退赔","未披露","其他"},
    "suspended": {True,False,None},
    "is_civil_criminal": {True,False,None},
    "is_organized": {True,False,None},
    "involves_blacklist_person": {True,False,None},
    "involves_insider": {True,False,None},
}

# list 值字段（token 级校验）
LIST_FIELDS = {
    "exit_mode": {"转卖变现","抵押/质押","其他","拒不归还/侵占","骗取资金/瓜分挥霍","自融吸储","循环换壳/拒退费"},
    "asset_authenticity": {"真实资产","虚构不存在","虚高估值","一物多融/重复融资","其他"},
}

# SOFT 枚举（不在域内仅 WARN，不阻断；来源类型可随检索增长）
SOFT_ENUM = {
    "source_type": {"裁判文书网判决书","新闻报道","最高检典型案例","各地法院公告","其他"},
}

SENTENCING_KW = re.compile(r"\d|年|月|缓刑|有期徒刑|无期徒刑|拘役|罚金")


def _as_list(v):
    """exit_mode / asset_authenticity 可能是 list 或 JSON 字符串，统一成 list。"""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("["):
            try:
                return json.loads(s)
            except Exception:
                return [s]
        return [s]
    return [str(v)]


def parse_stages(case_number):
    """返回 [(year, stage)]，stage: '终' / '初'(默认)。用于 A1 控制年份判定。"""
    stages = []
    if not case_number:
        return stages
    for m in re.finditer(r"\((\d{4})\)([^()]*?)(?=\(|$)", case_number):
        y = int(m.group(1))
        tail = m.group(2)
        stage = "终" if "终" in tail else "初"
        stages.append((y, stage))
    return stages


def _is_empty(v):
    return v is None or v == "" or (isinstance(v, (int, float)) and v == 0)


# restitution 字段是「金额/描述」混合：未退赔案件常填描述串「未退赔/无」而非 0。
_NO_AMOUNT_TOKENS = {"未退赔", "无", "无退赔", "未退", "零", "暂无", "不祥", "不详"}


def _no_amount(v):
    """是否为『无退赔金额』语义（空 / 0 / 描述性无金额串）。"""
    if v is None or v == "":
        return True
    if isinstance(v, (int, float)):
        return v == 0
    if isinstance(v, str):
        s = v.strip()
        if s in _NO_AMOUNT_TOKENS:
            return True
        try:
            return float(s) == 0
        except Exception:
            return False  # 非数字描述串 → 视为「无金额」之外的内容，交由下方规则判断
    return False


def _has_amount(v):
    return not _no_amount(v)


def validate_record(rec):
    cid = rec.get("case_id", "?")
    out = []  # (severity, code, case_id, msg)

    # --- SCHEMA ---
    keys = set(rec.keys())
    missing = [f for f in REQUIRED_FIELDS if f not in keys]
    extra = [f for f in keys if f not in REQUIRED_FIELDS]
    if missing:
        out.append(("HARD","SCHEMA",cid,f"缺失字段:{missing}"))
    if extra:
        out.append(("HARD","SCHEMA",cid,f"多余字段:{extra}"))

    # --- HARD 枚举域 ---
    for field, allowed in ALLOWED.items():
        if field not in rec:
            continue
        val = rec.get(field)
        if val not in allowed:
            out.append(("HARD","ENUM",cid,f"{field}='{val}' 不在允许域 {sorted([str(x) for x in allowed])}"))

    # --- list 字段 token 校验 ---
    for field, toks in LIST_FIELDS.items():
        vals = _as_list(rec.get(field))
        if not vals:
            out.append(("WARN","LIST",cid,f"{field} 为空列表/缺失"))
            continue
        bad = [t for t in vals if t not in toks]
        if bad:
            out.append(("HARD","ENUM",cid,f"{field} 含未登记 token:{bad}（允许:{sorted(toks)}）"))

    # --- SOFT 枚举 ---
    for field, allowed in SOFT_ENUM.items():
        val = rec.get(field)
        if val is not None and val not in allowed:
            out.append(("WARN","ENUM",cid,f"{field}='{val}' 不在已知来源类型，请确认后补登记"))

    # --- A1 案号控制年份 ↔ judgment_year ---
    jy = rec.get("judgment_year")
    stages = parse_stages(rec.get("case_number", ""))
    if stages and jy is not None:
        ctrl = None
        for y, st in stages:
            if st == "终":
                ctrl = y
        if ctrl is None:
            ctrl = stages[0][0]
        try:
            if int(ctrl) != int(jy):
                out.append(("HARD","A1",cid,f"案号控制年份{ctrl}≠judgment_year{jy}，须三源核验"))
        except Exception:
            out.append(("HARD","A1",cid,"judgment_year 非整数，无法与案号年份比对"))

    # --- A2 prison_term 禁 0 占位 ---
    pt = rec.get("prison_term")
    conv = rec.get("conviction")
    if isinstance(pt, (int, float)) and pt == 0 and conv not in (None, "", "无罪"):
        out.append(("HARD","A2",cid,"prison_term=0 占位（非无罪案）"))

    # --- A3 conviction 纯罪名 ---
    if isinstance(conv, str) and conv and SENTENCING_KW.search(conv):
        out.append(("HARD","A3",cid,f"conviction 混入刑期/缓刑字样:'{conv}'"))

    # --- A4 退赔标准 ↔ 金额 ---
    rs = rec.get("restitution_std")
    rest = rec.get("restitution")
    if rs in ("全额退赔", "部分退赔") and _no_amount(rest):
        out.append(("HARD","A4",cid,"退赔标准=全额/部分 但 restitution 无金额（应填具体退赔额）"))
    elif rs == "未退赔" and _has_amount(rest):
        out.append(("HARD","A4",cid,"退赔标准=未退赔 但 restitution 含金额，矛盾"))

    # --- C annotator 必填 ---
    if not rec.get("annotator"):
        out.append(("HARD","C",cid,"annotator 为空"))

    return out


def validate(records):
    allv = []
    for r in records:
        allv += validate_record(r)
    return allv


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


def main():
    path = _resolve_path()
    if not path or not os.path.isfile(path):
        print("未找到数据集文件（可传参：python3 validate_line_b.py <path>）")
        sys.exit(2)
    recs = load(path)
    fset = set(REQUIRED_FIELDS)
    viol = validate(recs)
    hard = [v for v in viol if v[0] == "HARD"]
    warn = [v for v in viol if v[0] == "WARN"]
    print(f"数据集: {os.path.basename(path)}")
    print(f"记录数: {len(recs)}  字段数: {len(fset)}")
    print(f"HARD 违规: {len(hard)}   WARN: {len(warn)}")
    if hard:
        byt = {}
        for sev, code, cid, msg in hard:
            byt.setdefault(code, []).append((cid, msg))
        for code, items in byt.items():
            print(f"  [{code}] {len(items)} 条")
            for cid, msg in items[:30]:
                print(f"    - {cid}: {msg}")
    if warn:
        byt = {}
        for sev, code, cid, msg in warn:
            byt.setdefault(code, []).append((cid, msg))
        for code, items in byt.items():
            print(f"  (WARN)[{code}] {len(items)} 条")
            for cid, msg in items[:15]:
                print(f"    - {cid}: {msg}")
    if hard:
        print("校验状态: 违规（阻断并入）")
        sys.exit(1)
    print("校验状态: PASS（无 HARD 违规；WARN 见上）")
    sys.exit(0)


if __name__ == "__main__":
    main()
