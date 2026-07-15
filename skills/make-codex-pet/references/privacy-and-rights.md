# Privacy, data flow, and likeness rights

Read this file before the first imagegen call in a run.

## One-time notice

Give one short informational notice such as: “这些参考图片和提示词会发送给 OpenAI 用于生成桌宠；它们不会上传到社区。生成内容可能保留在 Codex 任务服务端记录中，删除本地文件不能反向删除已有记录。” In English: the reference images and prompts are sent to OpenAI for generation and are not uploaded to the community. Generated content may remain in Codex task server-side records; deleting local files cannot retroactively delete existing server-side records.

Give the notice once, immediately before the first transfer. Do not ask the user to confirm it, turn it into a consent dialog, or wait for confirmation after sending it.

## Rights handling

The user remains responsible for having appropriate rights or permission to use supplied images and the resulting likeness. Do not require the user to confirm portrait authorization, ownership, or consent, and do not investigate those rights as a prerequisite. Do not impose a hard gate merely because the subject may be a celebrity or public figure.

Always follow the installed imagegen Skill and applicable OpenAI policy. If imagegen declines a request, explain the limitation briefly and offer a compliant adjustment; never bypass the refusal with a direct API or a different provider.

## Local handling

Run artifacts stay under `<codex-home>/pet-runs` unless the user explicitly supplied a run directory. The workflow does not upload references, prompts, previews, or the finished pet to any community. Installation copies only `pet.json` and `spritesheet.png` to the local pet package. Local cleanup removes local sensitive and reproducible work artifacts only; it cannot remove existing Codex task server-side records. Report any pending cleanup path.
