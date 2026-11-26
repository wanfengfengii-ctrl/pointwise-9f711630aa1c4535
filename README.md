# 河桥社区电台直播插播服务

录音师登记样本并发行固定用料。编排员提交可播时窗，现场值班员安排即播台标并逐拍推进。接班人按实播帧和原请求核对交班报告。服务使用 Python 3.11、SQLite 和本机 HTTP，无第三方依赖。

## 运行

```sh
python3 -B -m program.http_api --db ./radio.sqlite3 --port 8080
python3 -B check_project.py
python3 -B incident.py
```

第一条在 `127.0.0.1` 开启服务，使用已有库时保留录音、发行、场次、实播和报告。第二条使用随项目物化的标准回归入口运行完整 `tests` 套件。第三条在临时目录和操作系统分配的本机端口运行联动演练，无需先启动常驻服务。演练通过时输出由 HTTP 实测生成的 `POINTWISE_SCENARIO`。业务断言不符与运行错误分别报告。

## JSON HTTP 接口

以下接口均为 `POST`，请求体为 JSON 对象。除特别说明，成功响应为 HTTP 200。录音号、节目号、背景标识和请求号是非空字符串。草稿号、发行号、场次号、素材号和报告号是服务生成的正整数。单场素材号使用开场时选中的发行号。

| 路径 | 请求字段 | 响应 |
| --- | --- | --- |
| `/record` | `recording_id`, `frames` | `draft_id`, `recording_id`, `frame_count` |
| `/release` | `draft_id` | `release_id`, `draft_id`, `recording_id`, `frame_count` |
| `/open` | `program_id`, `release_ids`, 可选 `ident_release_ids`, 可选 `background_id` | `session_id`, `program_id`, `background_id`, `next_beat`, `assets` |
| `/preview` | `session_id`, `asset_id`, `window` | `status` 为 `suggested` 时附 `start_beat`，无解为 `unschedulable` |
| `/submit` | `session_id`, `request_id`, `asset_id`, `window` | 原申请各字段和 `status`，取值 `accepted`、`unschedulable`、`pending` |
| `/lookup` | `session_id`, `request_id` | 与 `/submit` 同形的原申请与状态 |
| `/ident` | `session_id`, `request_id`, `asset_id` | `status` 为 `started` 时附 `start_beat`，不可起播为 `unschedulable` |
| `/tick` | `session_id` | 本拍实播行 |
| `/timeline` | `session_id` | `session_id`, `outputs`，按实播拍号排序 |
| `/close` | `session_id` | `session_id`, `status: closed`, `report_id` |
| `/report` | `report_id` | `report_id`, `session_id`, `outputs`, `promises` |

开场表中的 `assets` 逐项含 `asset_id`、`release_id`、`recording_id`、`frame_count`、`kind`。`ident_release_ids` 是 `release_ids` 的子集，其余素材用于正式插播。省略背景标识时使用库中保存的默认值 `river-room`。空素材列表可用于仅播背景的场次。任一无效发行号会拒绝整次开场。

`frames` 是非空的有序整数数组，JSON 布尔值不是录音样本。时长为帧数。`window` 是 `[start, end]`，表示半开整数拍窗 `[start,end)`，要求 `start < end`。落在已播过去的部分不可用。预排只给建议。正式接纳允许挪动任何尚未起播的承诺，同时保全其许可窗。台标从当前待输出拍起占用完整时长。已起播片段优先续播。由调用方显式发送每次 `/tick`，服务不基于墙钟补拍。

实播行含 `beat`, `source`, `output_id`, `release_id`, `frame_index`, `sample`, `request_id`。`source` 为 `background`、`ident` 或 `promise`。背景行的 `output_id` 是背景标识，其他四个录音与请求字段为空。录音行的 `output_id` 是本场素材号，样本 `0` 保留。报告 `promises` 中的每项含原请求、素材和发行号、原窗口、实际 `beats`、实际 `first_beat`、实际 `end_beat`（不含）与 `fulfilled`。只有实际录音完全匹配原承诺才标记兑现。

参数、素材角色或窗不合法返回 400。未知草稿、发行、场次、编排请求或报告返回 404。同号编排异参、对关闭场次发起新操作或场次尚未播完即收班返回 409。错误响应含 `error` 和 `message`。业务不可排是正式裁定，仍返回 200。`pending` 返回 202，表示编排端尚未取得播出端裁定。同号同参提交可续办或复领，查询只取已有裁定。已接纳义务在结果送回编排台前就约束台标与走拍。收班和报告定稿同时提交，重复收班复领同一报告号。

## 一次值班演练

使用 `samples/shift.json` 登记并发行导语 A `[10,11]`、更正 B `[70,71]`、短台标 `[50]` 和长台标 `[51,52]`。开场后预排 A 于 `[0,5)`，建议起拍为 0。正式提交 A，再提交只能填满 `[1,3)` 的 B。接纳二者后，短台标可立即播出，实播值依次为 `50,70,71,10,11`。独立长台标对照在第 0 拍拒绝即播；如果长台标已先起播，迟到的 B 则不可排。演练还核对编排失答期间继续走拍、重开查回首次裁定，以及收班报告的实际帧和出处。测试夹具在真实播出提交前或提交后控制交件与回执时点，正常 HTTP 服务不提供设置这些时点的路由。
