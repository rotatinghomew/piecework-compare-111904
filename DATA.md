# 资料字典

CSV 为 UTF-8，第一行为列名。日期为北京时间，时间带偏移；资料可见时刻与业务发生时刻分别以字段含义为准。空值不能默认做零。

### `plots.csv`

坡度为度，运筐距离为米。`E` 是新进坡地，史料覆盖集中在晚班。

字段：`plot`、`slope_deg`、`haul_metres`、`surface`。

### `work.csv`

`picked_before_pct` 是本段开始时地块本季已经采走的估计比例，夜或午班不可与先采段无条件比较。

字段：`block`、`plot`、`team`、`started`、`ended`、`picked_before_pct`、`table_version`。

### `staff.csv`

出勤表示在此段名册登记，存在借工导致重叠，需用调令辨认真实工作。

字段：`worker`、`block`、`role`、`from_time`、`to_time`。

### `baskets.csv`

称重净公斤，装筐身份不重复。

字段：`basket`、`scale_kg`、`sealed_at`。

### `custody.csv`

待分标出候选段。一个版本中全筐有多个已签段时 `confirmed_kg` 为各段份额；只有一个已签段且为空表示整筐交接。

字段：`basket`、`block`、`ver`、`signed`、`known_at`、`confirmed_kg`。

### `waiting.csv`

等待从工效暴露中区分，不减少已确认出勤工资。

字段：`block`、`from_time`、`to_time`、`reason`。

### `loans.csv`

借工生效窗内以借入段计作业，剩余以原班实际签到为准；报价按对应作业段适用，下游可分别解释保底及绩效。

字段：`worker`、`home_block`、`borrowed_block`、`from_time`、`to_time`、`confirmed_at`。

### `pay_table.csv`

班组每作业段工资取计件与已确认累计工时保底较高者，非重叠小时按分钟折算；金额单位分，最终差额按分办。

字段：`version`、`cents_per_kg`、`floor_hour_cents`、`transfer_floor_party`。

### `payslips.csv`

已确认历史工资单，补证不改原金额。`evidence` 为当时记录版本。

字段：`document`、`block`、`cents`、`confirmed_at`、`evidence`。

### `policy.json`

仅供新季试算，不授权更改既有价目表。
