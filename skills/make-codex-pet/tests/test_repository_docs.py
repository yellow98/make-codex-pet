from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def read_project_file(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


class RepositoryDocsTests(unittest.TestCase):
    def test_required_open_source_files_exist(self) -> None:
        required = (
            "README.md",
            "PRIVACY.md",
            "ACCEPTABLE_USE.md",
            "LICENSE",
            "THIRD_PARTY_NOTICES.md",
            ".gitignore",
            ".github/workflows/test.yml",
        )

        missing = [path for path in required if not (PROJECT_ROOT / path).is_file()]
        self.assertEqual([], missing, f"missing repository files: {missing}")

    def test_readme_has_positioning_installation_and_runtime_boundaries(self) -> None:
        readme = read_project_file("README.md")
        self.assertIn("把这几张照片做成一个 Q 版桌宠", readme)
        self.assertIn("<codex-home>/skills/make-codex-pet", readme)
        self.assertRegex(readme, r"Codex.*GitHub|GitHub.*Codex")
        self.assertRegex(readme, r"一个 Skill|单个 Skill|Skill 单包")

        install_match = re.search(
            r"^##\s+安装\s*$([\s\S]*?)(?=^##\s+|\Z)",
            readme,
            flags=re.MULTILINE,
        )
        self.assertIsNotNone(install_match, "README must have a Chinese installation section")
        install = install_match.group(1)
        for forbidden in ("pip", "npm", "conda"):
            self.assertNotRegex(install, rf"(?i)\b{forbidden}\b")
        for dependency in ("Codex Desktop", "Pets", "imagegen", "Python", "Pillow"):
            self.assertIn(dependency, install)
        self.assertIn("ImageMagick", install)
        self.assertIn("API Key", install)
        self.assertIn("更新 Codex", install)

    def test_readme_documents_workflow_outputs_recovery_and_styles(self) -> None:
        readme = read_project_file("README.md")
        for phrase in (
            "3–5",
            "base",
            "一次确认",
            "9 行",
            "build",
            "validate",
            "视觉 QA",
            "install",
            "cleanup",
            "/pet",
            "auto",
            "q-cartoon",
            "pixel",
            "sticker",
            "<codex-home>/pet-runs",
            "<codex-home>/pets/<id>",
            "pet.json",
            "spritesheet.png",
            "manifest",
            "只续跑未完成行",
        ):
            self.assertIn(phrase, readme)

    def test_readme_documents_one_sentence_starter_install(self) -> None:
        readme = read_project_file("README.md")
        for phrase in (
            "安装这个 Skill 自带的经典宠物",
            "阿根廷10号",
            "葡萄牙7号",
            "挪威9号",
            "法国10号",
            "Settings > Pets > Refresh",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, readme)

    def test_readme_documents_data_flow_rights_compatibility_and_contributing(self) -> None:
        readme = read_project_file("README.md")
        for phrase in (
            "不上传到社区或维护者服务器",
            "OpenAI 图像服务",
            "Codex 任务服务端",
            "本地删除",
            "不能反向删除",
            "原图不会进入",
            "无遥测",
            "肖像",
            "明星",
            "必要权利",
            "发布",
            "商业使用",
            "维护者不审查",
            "不授权",
            "不背书",
            "非法律意见",
            "OpenAI 服务政策",
            "windows-latest",
            "ubuntu-latest",
            "旧版本",
            "故障",
            "贡献者",
            "项目结构",
            "非 OpenAI 官方",
            "Apache License 2.0",
            "English Summary",
        ):
            self.assertIn(phrase, readme)
        self.assertNotRegex(readme, r"(免责|免责声明).{0,30}(消除|免除).{0,10}(所有|全部).{0,10}责任")

    def test_privacy_explains_local_and_service_side_boundaries(self) -> None:
        privacy = read_project_file("PRIVACY.md")
        for phrase in (
            "<codex-home>/pet-runs",
            "<codex-home>/pets/<id>",
            "参考图",
            "提示词",
            "OpenAI 图像服务",
            "Codex 任务服务端",
            "本地删除",
            "不能反向删除",
            "cleanup_pending_path",
            "不上传到社区或维护者服务器",
            "无遥测",
            "API Key",
            "原图不会进入",
        ):
            self.assertIn(phrase, privacy)

    def test_acceptable_use_assigns_rights_and_downstream_responsibility(self) -> None:
        acceptable = read_project_file("ACCEPTABLE_USE.md")
        for phrase in (
            "必要权利",
            "冒充本人授权",
            "背书",
            "肖像权",
            "照片权利",
            "商标权",
            "后续发布",
            "商业使用",
            "用户自行负责",
            "维护者不审查",
            "非法律意见",
            "OpenAI 服务政策",
        ):
            self.assertIn(phrase, acceptable)
        self.assertNotRegex(acceptable, r"必须.{0,12}(确认|勾选|同意).{0,30}(才|方可)")

    def test_bundled_public_figures_have_no_endorsement_or_rights_grant(self) -> None:
        combined = read_project_file("README.md") + read_project_file("ACCEPTABLE_USE.md")
        for phrase in (
            "非官方 Q 版球星致敬形象",
            "项目原创宠物资产的著作权",
            "不代表任何本人或相关组织背书",
            "公开形象权",
            "人格权",
            "赞助关系",
            "商业使用权",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, combined)

    def test_license_is_apache_2_full_text(self) -> None:
        license_text = read_project_file("LICENSE")
        for phrase in (
            "Apache License",
            "Version 2.0, January 2004",
            "http://www.apache.org/licenses/",
            "1. Definitions.",
            "2. Grant of Copyright License.",
            "3. Grant of Patent License.",
            "4. Redistribution.",
            "5. Submission of Contributions.",
            "6. Trademarks.",
            "7. Disclaimer of Warranty.",
            "8. Limitation of Liability.",
            "9. Accepting Warranty or Additional Liability.",
            "END OF TERMS AND CONDITIONS",
            "APPENDIX: How to apply the Apache License to your work.",
        ):
            self.assertIn(phrase, license_text)
        self.assertGreater(len(license_text), 10_000)

    def test_third_party_notices_has_official_links_and_independence_notice(self) -> None:
        notices = read_project_file("THIRD_PARTY_NOTICES.md")
        for link in (
            "https://learn.chatgpt.com/docs/pets",
            "https://github.com/openai/skills/tree/main/skills/.curated/hatch-pet",
            "https://github.com/openai/skills/blob/main/skills/.curated/hatch-pet/LICENSE.txt",
        ):
            self.assertIn(link, notices)
        self.assertIn("公开合同与工作流", notices)
        self.assertRegex(notices, r"不隶属.{0,20}OpenAI|OpenAI.{0,20}不隶属")
        self.assertRegex(notices, r"不受.{0,20}OpenAI.{0,20}背书|OpenAI.{0,20}不.{0,20}背书")

    def test_ci_covers_windows_ubuntu_utf8_and_pinned_pillow(self) -> None:
        workflow = read_project_file(".github/workflows/test.yml")
        for phrase in (
            "windows-latest",
            "ubuntu-latest",
            "3.12",
            "Pillow==11.0.0",
            "PYTHONUTF8",
            "python -X utf8 -m unittest discover -s skills/make-codex-pet/tests -v",
            "test_skill_contract.py",
        ):
            self.assertIn(phrase, workflow)
        self.assertRegex(workflow, r"PYTHONUTF8:\s*[\"']?1[\"']?")
        self.assertNotRegex(workflow, r"(?i)secrets?\s*[.:\[]")

    def test_gitignore_covers_local_python_and_pet_run_artifacts(self) -> None:
        gitignore = read_project_file(".gitignore")
        for pattern in (
            ".venv/",
            "venv/",
            "__pycache__/",
            "*.py[cod]",
            ".codex_tmp/",
            "pet-runs/",
        ):
            self.assertIn(pattern, gitignore)
        for project_file in ("README.md", "PRIVACY.md", "ACCEPTABLE_USE.md", "LICENSE"):
            self.assertNotIn(project_file, gitignore)


if __name__ == "__main__":
    unittest.main()
