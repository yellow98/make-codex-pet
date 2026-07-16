# Make Codex Pet

把参考照片做成可在 Codex Desktop 里运行的本地动画桌宠：例如，`把这几张照片做成一个 Q 版桌宠`。

这是一个面向 Codex Desktop 的单 Skill 项目。它从照片中提炼角色特征，生成并校验九行动画素材，安装为 Codex Pets 包，最后让用户通过 `/pet` 使用。

## 安装

把仓库中的 `skills/make-codex-pet` 整个复制或安装到 `<codex-home>/skills/make-codex-pet`。未来也可以让 Codex 直接从 GitHub 安装。它始终是一个 Skill，Skill 单包不分平台；Windows 与 Linux 的路径、命令和运行时差异都由 Skill 内部处理。

普通用户不需要另行安装 Python、Node 或 ImageMagick，也不需要填写 API Key。运行依赖 Codex Desktop 已有的 Pets、imagegen，以及 Codex 随附的 bundled Python/Pillow；若这些能力缺失，请先更新 Codex。仓库根目录的 `pyproject.toml` 仅供贡献者和 CI 描述测试环境，不是最终用户的安装入口。

安装 Skill 后，如需一次装入仓库自带的四个经典宠物，在 Codex 中直接说：

> 安装这个 Skill 自带的经典宠物

Skill 会把阿根廷10号、葡萄牙7号、挪威9号和法国10号安装到当前用户的 Codex Pets 目录；四款都是原创脸的球星致敬角色，这一步不调用 imagegen。完成后打开 **Settings > Pets > Refresh**，选择角色，再输入 `/pet` 唤起。

## 使用方式

在 Codex Desktop 中附上一个或多个参考图，然后直接说：

> 把这几张照片做成一个 Q 版桌宠

也可以指定风格：

- `auto`：未指定时自动选择。
- `q-cartoon`：Q 版卡通。
- `pixel`：像素风。
- `sticker`：贴纸风。

Skill 单包支持这四种风格，不为不同系统拆分安装包。

## 工作流

1. 自动从参考图提炼 3–5 个强识别特征，并建立本地 run manifest。
2. 先生成一张 base 角色预览，只设置一次确认门；用户认可角色后，后续不再要求逐行确认。
3. 自动生成 9 行动作素材；每个完成状态立刻写入 manifest。
4. 依次执行 build、validate 和视觉 QA，检查帧数、透明度、边缘、角色一致性与动作可读性。
5. 通过后执行本地 install，再执行 cleanup；若清理仍被文件占用，会保留可追踪的 pending 路径。
6. 安装完成后，在 Codex Desktop 输入 `/pet` 选择或启用桌宠。

## 本地路径与最终输出

- 临时运行目录：`<codex-home>/pet-runs`。
- 已安装包目录：`<codex-home>/pets/<id>`。
- 安装后的包严格只有两个文件：`pet.json` 与 `spritesheet.png`。

参考原图、提示词、base 预览和九行中间图只属于运行目录；原图不会进入最终 package。成功清理后会移除敏感和可再生产物，只保留脱敏摘要、最终精灵图及 QA 预览；若 cleanup 返回 pending，则按故障说明处理。

## 中断恢复

每次启动前，Skill 会检查 `<codex-home>/pet-runs` 下的 manifest。它只恢复与当前角色匹配的最新未完成 run，并只续跑未完成行；已经标记完成的 base 或动作行不会重复生成，也不会覆盖无关 run。

如果跨任务恢复时 base 是否已获确认不明确，Skill 会展示已记录的 base 并等待确认，不会重新生成。若某一行传输或记录失败，该行最多自动重试一次；再次失败后停止并报告具体行与错误，下一次仍按 manifest 恢复。

## 数据流与隐私

- 项目不上传到社区或维护者服务器：参考图、提示词、生成图片和 Pets 包都不会进入维护者自建接收端。
- 为完成生成，参考图与提示词会发送给 OpenAI 图像服务。生成内容及任务上下文可能在 Codex 任务服务端记录，具体以所用 OpenAI 服务的规则和账户设置为准。
- 删除 `<codex-home>/pet-runs` 或 `<codex-home>/pets/<id>` 只删除本地副本；本地删除不能反向删除已经存在的服务端记录。
- 仓库脚本无遥测，不读取或索取 API Key，也不包含社区上传逻辑。
- 原图不会进入安装 package；最终包只有上述两个生成文件。

完整边界见 [PRIVACY.md](PRIVACY.md)。

## 肖像、明星与后续使用

用户负责确保自己对照片、人物肖像、标志及其他输入拥有必要权利或许可，并对生成结果的后续发布、分享和商业使用自行负责。明星或其他公众人物并不构成例外。

仓库自带的经典宠物是使用原创脸的非官方 Q 版球星致敬形象，仅用于演示 Skill 的本地 Pets 能力，不代表任何本人或相关组织背书，也不构成赞助关系。Apache License 2.0 只覆盖仓库代码和文档，不授予任何人物的公开形象权、人格权、姓名或肖像相关权利、第三方商标权或商业使用权。

维护者不审查每一次输入或输出，不授权任何人物、照片或商标的使用，也不背书任何具体结果，更不代表相关人物、权利人或 OpenAI 作出背书。本说明与 [ACCEPTABLE_USE.md](ACCEPTABLE_USE.md) 均为项目边界说明，非法律意见；适用法律下的责任范围应由具体事实与法律确定，本文不保证其可被完全排除。OpenAI 服务政策仍适用。

这些说明不是强制确认步骤，也不是 Skill 的运行硬拦截。使用者需要自行作出权利判断。

## 兼容性

平台差异在一个 Skill 内部处理。CI 使用 Python 3.12 在 `windows-latest` 和 `ubuntu-latest` 上运行测试，但这不等于承诺支持任意 Windows 环境、任意 Linux 发行版或所有 Codex 旧版本。

可用的 Codex Desktop Pets、imagegen 和 bundled Python/Pillow 是运行前提。路径权限、企业策略、版本缺口或损坏的本地运行时仍可能导致失败；优先更新 Codex 后再重试。

## 输出、恢复与常见故障

- **生成中断**：保留 run 目录，不要手工改 manifest；重新发起同一角色任务会只续跑未完成行。
- **校验失败**：查看 validate 返回的结构化错误和 QA 图。Skill 会按问题行修复，不会重新生成已通过的行。
- **安装失败**：确认 Codex Desktop 可写 `<codex-home>/pets`，并检查目标 `<id>` 是否冲突。
- **cleanup pending**：Skill 会重试一次；仍 pending 时会报告 `cleanup_pending_path`。关闭占用文件的预览器或进程后，仅删除该报告路径，不要清理其他 run。
- **`/pet` 看不到新角色**：确认包目录只有 `pet.json` 与 `spritesheet.png`，然后更新或重新打开 Codex Desktop。
- **缺少 imagegen、Pets 或 bundled runtime**：更新 Codex；普通用户不应改用自备运行时或单独拼装依赖。

## 贡献者

开发与 CI 才使用 `pyproject.toml` 和 Python 依赖管理。建议使用 Python 3.12，并固定与 CI 相同的 Pillow：

```powershell
python -m pip install Pillow==11.0.0
python -X utf8 -m unittest discover -s skills/make-codex-pet/tests -v
python -X utf8 -m unittest discover -s skills/make-codex-pet/tests -p "test_skill_contract.py" -v
```

提交前还应对脚本运行 `py_compile`，并以 UTF-8 模式运行 `scripts/quick_validate.py` 的快速校验。测试和校验只在仓库与临时目录内读写，不需要 secret，也不会写入外部服务。

## 项目结构

```text
make-codex-pet/
├── skills/make-codex-pet/
│   ├── SKILL.md              # Codex 编排合同
│   ├── agents/               # 可选的行生成工作者合同
│   ├── assets/starter-pets/  # 可选安装的四个经典宠物
│   ├── references/           # Pets、图像与隐私参考
│   ├── scripts/              # prepare/build/validate/install/cleanup/starter install
│   └── tests/                # unittest 合同与脚本测试
├── .github/workflows/test.yml
├── ACCEPTABLE_USE.md
├── PRIVACY.md
├── THIRD_PARTY_NOTICES.md
└── LICENSE
```

## 项目身份、商标与许可

本项目是独立社区项目，非 OpenAI 官方项目，不隶属于 OpenAI，也不受 OpenAI 背书。Codex、OpenAI 及其他名称和标志属于各自权利人；提及它们仅用于说明兼容性，不授予商标许可。

项目代码与文档按 [Apache License 2.0](LICENSE) 提供。该许可不授予任何照片、人物肖像、生成内容或第三方商标的权利。第三方参考见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## English Summary

Make Codex Pet is an independent, Apache-2.0-licensed Codex Skill that turns reference images into a locally installed Codex Desktop pet. End users rely on the Pets, imagegen, and bundled Python/Pillow capabilities already provided by Codex; no separate runtime or API key is required. Reference images and prompts are sent to OpenAI image services, while project scripts add no telemetry or community upload.
