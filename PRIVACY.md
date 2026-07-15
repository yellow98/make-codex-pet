# Privacy

本文说明 Make Codex Pet 在生成、安装、恢复和清理过程中涉及的数据边界。它描述的是项目行为，不替代 OpenAI 服务本身的隐私条款或账户设置。

## 数据会在哪里

本地中间产物默认位于 `<codex-home>/pet-runs`。其中可能包括：

- 用户提供的参考图副本或其本地路径记录；
- 从照片提炼的角色特征、提示词与 manifest；
- base 预览、九行动作图片、日志、校验结果和视觉 QA 图；
- 安装前的 package staging 内容。

安装完成的桌宠位于 `<codex-home>/pets/<id>`，且最终目录只含 `pet.json` 与 `spritesheet.png`。原图不会进入最终 package，也不会复制到已安装目录。

## OpenAI 服务数据流

生成 base 和动作行时，参考图与提示词会发送给 OpenAI 图像服务。生成图片、提示词和相关任务上下文也可能在 Codex 任务服务端记录。实际的服务端保留、控制和删除能力取决于所使用的 OpenAI 服务、账户类型、设置与适用政策；本项目无法替代或扩展这些控制。

删除本地 `<codex-home>/pet-runs`、已安装的 `<codex-home>/pets/<id>`，或其他本地缓存，只影响设备上的副本。本地删除不能反向删除已存在的 OpenAI 服务端记录；如需处理服务端数据，应使用对应 OpenAI 产品和账户提供的控制方式。

## 项目不接收的数据

项目不上传到社区或维护者服务器：参考图、提示词、生成图片、manifest 和桌宠包都不会进入这类接收端。项目没有自建云端处理接口、社区图库或维护者收集端。

仓库脚本无遥测，不发送使用统计、错误报告或设备标识，不读取浏览器会话，也不要求或收集 API Key。图像生成由 Codex Desktop 已有的 imagegen 能力发起，而不是由脚本直连图像 API。

## 本地保留与 cleanup

运行期间，本地文件会保留到构建、validate、视觉 QA 和 install 完成，以支持安全恢复。每个动作完成后会立即记入 manifest；中断后只续跑未完成行。

安装成功后，cleanup 会删除 run 目录中的参考路径、提示词、动作行和切帧等敏感或可再生产物，并保留脱敏摘要、最终精灵图和 QA 预览。如果文件仍被占用，cleanup 会重试一次；仍无法删除时返回 `status: "pending"` 和精确的 `cleanup_pending_path`。这个 pending 路径会继续保留参考图或中间生成物，直到用户关闭占用进程并手工删除所报告的确切目录。不要扩大删除范围到其他 run 或已安装的 Pets。

用户也可以选择保留未完成 run 以便恢复。手工删除 run 会失去恢复所需的 manifest 和中间结果，但仍不会触发服务端删除。

## 用户可以做什么

- 生成前仅提供完成任务所需的参考图，并避免无关敏感背景信息。
- 生成后检查 `<codex-home>/pet-runs` 和 `<codex-home>/pets/<id>` 的本地内容。
- 需要恢复时保留 manifest；确认不再恢复后再执行 cleanup。
- cleanup pending 时只处理 `cleanup_pending_path` 指向的目录。
- 通过所使用的 OpenAI 产品或账户设置管理可能存在的 Codex 任务服务端记录。

## 边界

本项目的无社区上传、无遥测与无 API Key 收集承诺只覆盖本仓库提供的 Skill 和脚本。Codex Desktop、imagegen、操作系统、用户安装的其他扩展及 OpenAI 服务各自可能有独立的数据处理规则，应分别查看其政策。
