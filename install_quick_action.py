#!/usr/bin/env python3
"""
Finder 우클릭 > 빠른 동작(서비스)에 '한국어 자막 만들기'를 설치/제거합니다.

    ./install_quick_action.py              # 설치
    ./install_quick_action.py --uninstall  # 제거
"""

import argparse
import plistlib
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

NAME = "한국어 자막 만들기"
WORKFLOW = Path.home() / "Library/Services" / f"{NAME}.workflow"
LOG = Path.home() / "Library/Logs/make_subtitle.log"
SCRIPT = Path(__file__).resolve().parent / "make_subtitle.py"


def shell_command() -> str:
    # Automator는 PATH가 최소한이라 brew 경로를 직접 추가하고,
    # 설치에 쓴 python(3.10+)을 그대로 사용한다 (/usr/bin/python3는 3.9일 수 있음).
    py, script, log = (shlex.quote(str(p)) for p in (sys.executable, SCRIPT, LOG))
    return f"""export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
for f in "$@"; do
  name="${{f:t}}"
  echo "===== $(date) $f" >> {log}
  if {py} {script} "$f" >> {log} 2>&1; then
    osascript -e "display notification \\"$name\\" with title \\"자막 생성 완료\\""
  else
    osascript -e "display notification \\"$name (로그: ~/Library/Logs/make_subtitle.log)\\" with title \\"자막 생성 실패\\""
  fi
done
"""


def info_plist() -> dict:
    return {
        "NSServices": [{
            "NSMenuItem": {"default": NAME},
            "NSMessage": "runWorkflowAsService",
            "NSRequiredContext": {"NSApplicationIdentifier": "com.apple.finder"},
            "NSSendFileTypes": ["public.movie"],
        }]
    }


def document_wflow() -> dict:
    string_list = {"Container": "List", "Types": ["com.apple.cocoa.string"]}
    action = {
        "AMAccepts": {**string_list, "Optional": True},
        "AMProvides": string_list,
        "AMActionVersion": "2.0.3",
        "AMApplication": ["Automator"],
        "AMParameterProperties": {k: {} for k in ("COMMAND_STRING", "CheckedForUserDefaultShell", "inputMethod", "shell", "source")},
        "ActionBundlePath": "/System/Library/Automator/Run Shell Script.action",
        "ActionName": "Run Shell Script",
        "ActionParameters": {
            "COMMAND_STRING": shell_command(),
            "CheckedForUserDefaultShell": True,
            "inputMethod": 1,  # 1 = 인수로 전달
            "shell": "/bin/zsh",
            "source": "",
        },
        "BundleIdentifier": "com.apple.RunShellScript",
        "CFBundleVersion": "2.0.3",
        "CanShowSelectedItemsWhenRun": False,
        "CanShowWhenRun": True,
        "Category": ["AMCategoryUtilities"],
        "Class Name": "RunShellScriptAction",
        "InputUUID": str(uuid.uuid4()).upper(),
        "OutputUUID": str(uuid.uuid4()).upper(),
        "UUID": str(uuid.uuid4()).upper(),
        "UnlocalizedApplications": ["Automator"],
        "isViewVisible": 1,
    }
    return {
        "AMApplicationBuild": "523",
        "AMApplicationVersion": "2.10",
        "AMDocumentVersion": "2",
        "actions": [{"action": action, "isViewVisible": 1}],
        "connectors": {},
        "workflowMetaData": {
            "serviceApplicationBundleID": "com.apple.finder",
            "serviceInputTypeIdentifier": "com.apple.Automator.fileSystemObject.movie",
            "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
            "serviceProcessesInput": 0,
            "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
        },
    }


def refresh_services() -> None:
    subprocess.run(["/System/Library/CoreServices/pbs", "-update"], check=False)


def install() -> None:
    if sys.version_info < (3, 10):
        sys.exit(f"Python 3.10 이상이 필요합니다 (현재 {sys.executable} {sys.version.split()[0]}). 예: /opt/homebrew/bin/python3 {Path(__file__).name}")
    if WORKFLOW.exists():
        shutil.rmtree(WORKFLOW)
    contents = WORKFLOW / "Contents"
    contents.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps(info_plist()))
    (contents / "document.wflow").write_bytes(plistlib.dumps(document_wflow()))
    refresh_services()
    print(f"설치 완료: {WORKFLOW}")
    print(f"Finder에서 영상 선택 > 우클릭 > 빠른 동작(또는 서비스) > {NAME}")
    print(f"로그: {LOG}")


def uninstall() -> None:
    if not WORKFLOW.exists():
        print("설치된 빠른 동작이 없습니다.")
        return
    shutil.rmtree(WORKFLOW)
    refresh_services()
    print(f"제거 완료: {WORKFLOW}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--uninstall", action="store_true", help="설치된 빠른 동작 제거")
    uninstall() if parser.parse_args().uninstall else install()
