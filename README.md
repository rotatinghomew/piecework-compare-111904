# 采摘工效与计件复核

封筐和班组价目均为合成记录，分摊与薪酬不作真实用工依据。

`data` 保存原始记录，`DATA.md` 是资料字典。本仓库在原始契约检查之外，提供一套
**只读、按交班日可复现** 的分析应用（`harvest/`），用于回答三类问题：

1. 领班：一筐或一段借工为什么算不进去；
2. 工资复核员：同一次申诉对几个班的应付影响（已结工资单不改写，只发差额凭证）；
3. 经营者：困难地补贴建议能覆盖哪些工作、哪些地对照不足，以及新口径成本区间。

## 不可违背的口径

- **时点隔离**：所有结论只采用 `known_at/confirmed_at` 不晚于交班日 cutoff 的记录版本；
  后补归属改善以后的分析，但不回写历史报表（报表文件名带 cutoff 时间戳，CSV 前两行标注证据范围）。
- **借工去重**：借窗内分钟计借入段、从借出段剔除；调令未确认时重叠分钟两段都不计有效劳动，
  列 `unallocated_min`，不按时长硬分。
- **停等分离**：停等保留在计保底的 `paid_min`，但从工效暴露 `exposure_min` 剔除。
- **混筐归属**：已签份额/整筐交接为实数；仅知两班共用的待分筐只给可能范围
  `[0, 净重]`，计件差额挂起，禁止用工作时长分摊后充作实数。
- **价目表分段适用**：计件价与工时保底只取作业段自身 `table_version`；试价、未批准新价
  不回溯历史申诉；申诉确认后只发差额凭证，不覆盖原工资单。
- **共享筐双班联动**：归属变化时同时重算两个班的保底与计件，禁止以一班多付抵另一班应补。
- **可比性**：早班先采段（`picked_before_pct=0`）与午后熟果窗不无条件比较；工种分列；
  班组名称相似不构成可比；E 地只有午后段，仅可受限试排。
- **新季两套成本**：原班表口径（会涨多少）与可调人工口径（能多完成多少有证据的工作）
  分列；同一工人共同时间段不重复占人；已签最低劳时不可撤回，待定协议不占最终额度。

## 运行

环境使用 Python 3.11.2，仅用标准库（`requirements.txt` 的 numpy/scipy 非本应用所需）。

```sh
# 原始资料契约检查
python3 -m unittest discover -s tests -v

# 领域规则测试（隔离模式，不加载 cwd/PYTHONPATH）
python3 -I tests/test_domain_rules.py

# 按 policy.json 的两个交班日各出一套报表到 reports/
python3 -I scripts/run_report.py
python3 -I scripts/run_report.py --asof cut1
python3 -I scripts/run_report.py --list-cutoffs
```

检查与报表只读取本地 `data/`，不访问业务系统，也不写回任何原始记录。

## 产物（reports/）

每个 cutoff 生成一套，文件名含 cutoff 时间戳：

| 文件前缀 | 视角 | 内容 |
|---|---|---|
| `foreman_labor_*` / `foreman_explain_*` | 领班 | 逐段 paid/exposure/unallocated 分钟、实数与可能范围公斤、待分筐与借工说明 |
| `auditor_payroll_*` / `auditor_recheck_*` | 复核员 | 分段计件/保底/应付、工资单差额凭证、争议准备金时间线（同号覆盖） |
| `operator_productivity_*` / `operator_costs_*` / `operator_subsidy_*` | 经营者 | 分地块分时段分工种工效、补贴证据分级、两套加价成本区间、准备金与不可承诺项 |

## 代码结构

```
harvest/
  dataio.py        原始记录加载与硬校验（身份唯一、时间窗、份额不超净重等）
  evidence.py      cutoff 时点证据范围、最新可见归属版本
  labor.py         借工去重、停等分离、有效劳时重建
  custody.py       封筐实数归属与待分可能范围（严格分开）
  payroll.py       分段价目重算、工资单复核、差额凭证
  productivity.py  可比工效分组与补贴证据分级
  season.py        新季人工冲突、试排上限、两套成本、共同准备金
  reports.py       三类视角 CSV/Markdown 报表
scripts/run_report.py  命令行入口
tests/test_domain_rules.py  口径回归测试
```
