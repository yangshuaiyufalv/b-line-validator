# b-line-validator

B 线（融资租赁合同诈骗案例）**校验器与状态基准**脚本 —— 自动化任务「B线-融资租赁新案发现」（cloud:5309206）的版本化代码库。

## 仓库内容

| 文件 | 作用 |
|------|------|
| `scripts/validate_line_b.py` | schema + 一致性校验器。46 字段强制、C1/C2/C4 枚举、B 系列枚举（list 字段 token 级）、A1 案号控制年份↔judgment_year、A2 禁 prison_term=0 占位、A3 conviction 纯罪名、A4 退赔标准↔金额、annotator 必填。**退出码 1 = 有 HARD 违规**（供自动化判定"阻断并入"） |
| `scripts/b_line_stats.py` | 状态基准输出：从数据集文件名动态解析版本、记录数、46 字段、手法/资产/法院/来源分布、C1/C2/C4 缺口、A1 冲突计数、低频手法与最少资产类型检索优先级、校验状态、索引一致性。**含版本漂移自检**（数据集版本 ≠ `VALIDATOR_VERSION` 时报警） |

## 版本约定

- 数据集文件名形如 `融资租赁合同诈骗案例特征库_v3.0.jsonl.txt`，`vX.Y` 即当前特征库版本。
- 校验器内 `VALIDATOR_VERSION` 是**逻辑对应的 schema 版本**。
- 二者不一致时，`b_line_stats.py` 会在报告里打印 `⚠ 校验器版本(vX.Y) 与数据集版本(vA.B) 不一致` —— 提示需要更新校验器（加字段 / 改枚举 / 改规则）。

## 版本演进流程（3.0 → 3.1 → 4.0 …）

1. 数据集加字段 / 改枚举 / 改规则时，同步改 `scripts/` 下脚本：
   - 新字段 → 加进 `REQUIRED_FIELDS`；
   - 新枚举值 → 更新 `ALLOWED` / `LIST_FIELDS` / `SOFT_ENUM`；
   - 新规则 → 在 `validate_record()` 增加检查（沿用 `HARD`/`WARN` 分级）；
   - 最后把 `VALIDATOR_VERSION` 改成新版本号。
2. `python3 -m py_compile scripts/*.py` 自检通过。
3. 本地验证：`cd <含 data/output 的目录> && python3 scripts/validate_line_b.py`。
4. `git push`。
5. 若自动化已改为"运行时 clone 本仓库"，**无需改 prompt**，下次运行自动用新版；若仍是 prompt 内嵌，则需把新脚本重新嵌入并更新任务。

## 给自动化任务的接入方式

自动化运行时（云端沙箱）执行：

```bash
git clone --depth 1 https://github.com/<owner>/b-line-validator.git /tmp/blv \
  && cp /tmp/blv/scripts/*.py scripts/ \
  && python3 -m py_compile scripts/validate_line_b.py scripts/b_line_stats.py
```

公开仓库无需 token。若沙箱出网无法访问 github.com，则退回使用 prompt 内嵌脚本兜底。

## 用法

```bash
# 状态基准（自动在工作区定位 融资租赁合同诈骗案例特征库_v*.jsonl.txt）
python3 scripts/b_line_stats.py

# 校验器（退出码 1 = 有 HARD 违规，阻断并入）
python3 scripts/validate_line_b.py
python3 scripts/validate_line_b.py /path/to/融资租赁合同诈骗案例特征库_v3.0.jsonl.txt
```
