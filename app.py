import ast
import difflib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="CodeMedic AI", page_icon="🩺", layout="wide")

st.markdown("""
<style>
.stApp {background: radial-gradient(circle at 8% 0%, #13243a, #090d19 55%); color:#eef2ff;}
[data-testid="stSidebar"] {background:#10192b;}
.hero {padding:24px;border-radius:20px;background:linear-gradient(120deg,#123b50,#31245c);border:1px solid #ffffff20;margin-bottom:16px;}
.hero h1 {margin:0;font-size:2.4rem;}
.hero p {color:#d1d9ec;margin-bottom:0;}
div[data-testid="stMetric"] {background:#ffffff09;border:1px solid #ffffff18;padding:12px;border-radius:14px;}
</style>
""", unsafe_allow_html=True)

SAMPLES = {
    "Python": "def divide(a, b):\n    try:\n        return a / b\n    except:\n        return 0\n\nprint(divide(10, 2))\n",
    "Java": 'public class Main {\n    public static void main(String[] args) {\n        int answer = 10 / 2;\n        System.out.println("Answer: " + answer);\n    }\n}\n',
    "C": '#include <stdio.h>\n\nint main(void) {\n    int answer = 10 / 2;\n    printf("Answer: %d\\\\n", answer);\n    return 0;\n}\n',
    "C++": '#include <iostream>\nusing namespace std;\n\nint main() {\n    int answer = 10 / 2;\n    cout << "Answer: " << answer << endl;\n    return 0;\n}\n',
    "JavaScript": "function addNumbers(a, b) {\n    return a + b;\n}\n\nconsole.log(addNumbers(5, 3));\n",
}

def make_finding(severity, title, message, line=None, source="CodeMedic"):
    return {"severity": severity, "title": title, "message": message, "line": line, "source": source}

def find_line(code, pattern):
    for number, line in enumerate(code.splitlines(), start=1):
        if re.search(pattern, line):
            return number
    return None

def run_check(command, timeout=12, input_text=None):
    """Run a syntax checker/linter only; never execute submitted source code."""
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout,
            shell=False, input=input_text
        )
        return result.returncode, (result.stdout + "\n" + result.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "The checker timed out and was stopped."
    except FileNotFoundError:
        return 127, "Required tool was not found."
    except Exception as exc:
        return 125, f"Could not start checker: {exc}"

def analyze_python(code):
    findings = []
    try:
        ast.parse(code)
    except SyntaxError as exc:
        findings.append(make_finding(
            "Error", "Python syntax error",
            f"{exc.msg}. Check brackets, colons, quotes, and indentation near line {exc.lineno}.",
            exc.lineno, "Python AST"
        ))
        return findings, False, "Ruff checks were skipped because Python parsing failed."

    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            findings.append(make_finding(
                "Warning", "Bare except block",
                "Catch a specific exception instead of every exception, so unexpected problems are not hidden.",
                node.lineno, "Python AST"
            ))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("eval", "exec"):
            findings.append(make_finding(
                "Warning", f"Use of {node.func.id}()",
                "Dynamic code execution can be risky with untrusted input. Prefer an explicit, safer alternative.",
                node.lineno, "Python AST"
            ))
        if isinstance(node, ast.FunctionDef) and len(node.args.args) > 6:
            findings.append(make_finding(
                "Info", "Function has many parameters",
                "Consider grouping related values or splitting the function into smaller parts.",
                node.lineno, "Python AST"
            ))

    ruff_message = ""
    with tempfile.TemporaryDirectory() as temp_dir:
        source = Path(temp_dir) / "reviewed_code.py"
        source.write_text(code, encoding="utf-8")
        rc, output = run_check([
            os.sys.executable, "-m", "ruff", "check", "--output-format", "json", str(source)
        ])
        if rc in (0, 1) and output:
            try:
                for issue in json.loads(output):
                    findings.append(make_finding(
                        "Warning",
                        f"Ruff {issue.get('code', 'finding')}: {issue.get('message', 'Review this issue')}",
                        issue.get("message", "Review this code-quality issue."),
                        issue.get("location", {}).get("row"), "Ruff"
                    ))
            except json.JSONDecodeError:
                pass
        elif rc == 127:
            ruff_message = "Ruff is not installed in this environment. Install it with: python -m pip install ruff"

    if not findings:
        findings.append(make_finding(
            "Success", "No selected issues detected",
            "Python syntax is valid and the basic CodeMedic checks found no selected patterns.",
            None, "Python AST"
        ))
    return findings, True, ruff_message

def analyze_other(language, code):
    findings, messages, syntax_ok = [], [], None
    with tempfile.TemporaryDirectory() as temp_dir:
        folder = Path(temp_dir)
        if language == "Java":
            match = re.search(r"\bpublic\s+class\s+([A-Za-z_$][A-Za-z0-9_$]*)", code)
            source = folder / ((match.group(1) if match else "Main") + ".java")
            source.write_text(code, encoding="utf-8")
            if not shutil.which("javac"):
                messages.append("Java checker unavailable. Install a JDK and ensure javac is on PATH.")
            else:
                rc, output = run_check(["javac", "-proc:none", "-d", str(folder), str(source)])
                syntax_ok = rc == 0
                findings.append(make_finding(
                    "Success" if syntax_ok else "Error",
                    "Java compile check passed" if syntax_ok else "Java compile check failed",
                    "javac accepted the source; the program was not executed." if syntax_ok else (output or "javac reported an error."),
                    None, "javac"
                ))
        elif language in ("C", "C++"):
            extension = ".c" if language == "C" else ".cpp"
            source = folder / ("reviewed_code" + extension)
            source.write_text(code, encoding="utf-8")
            if language == "C":
                compiler = shutil.which("gcc") or shutil.which("clang")
            else:
                compiler = shutil.which("g++") or shutil.which("clang++")
            if not compiler:
                messages.append(f"{language} checker unavailable. Install GCC/Clang (C) or G++/Clang++ (C++) and ensure it is on PATH.")
            else:
                command = [compiler, "-fsyntax-only", str(source)]
                if language == "C++":
                    command.insert(1, "-std=c++17")
                rc, output = run_check(command)
                syntax_ok = rc == 0
                findings.append(make_finding(
                    "Success" if syntax_ok else "Error",
                    f"{language} syntax check passed" if syntax_ok else f"{language} syntax check failed",
                    "Compiler accepted the source in syntax-only mode; it was not executed." if syntax_ok else (output or "Compiler reported an error."),
                    None, Path(compiler).name
                ))
        elif language == "JavaScript":
            source = folder / "reviewed_code.js"
            source.write_text(code, encoding="utf-8")
            node = shutil.which("node")
            if not node:
                messages.append("JavaScript checker unavailable. Install Node.js and ensure node is on PATH.")
            else:
                rc, output = run_check(["node", "--check", str(source)])
                syntax_ok = rc == 0
                findings.append(make_finding(
                    "Success" if syntax_ok else "Error",
                    "JavaScript syntax check passed" if syntax_ok else "JavaScript syntax check failed",
                    "Node accepted the file with --check; the program was not executed." if syntax_ok else (output or "Node reported a syntax error."),
                    None, "node --check"
                ))

    for pattern, severity, title, message in [
        (r"\bTODO\b|\bFIXME\b", "Info", "TODO/FIXME marker",
         "This may indicate unfinished work; confirm it is completed before submission."),
        (r"password\s*=\s*['\"][^'\"]+['\"]|api[_-]?key\s*=\s*['\"][^'\"]+['\"]", "Warning",
         "Possible hard-coded secret",
         "Do not store real passwords or API keys in source code. Use environment variables or a secret manager."),
    ]:
        line = find_line(code, pattern)
        if line:
            findings.append(make_finding(severity, title, message, line, "Basic pattern check"))

    if not findings:
        findings.append(make_finding(
            "Info", "No checker result available",
            "Install the language tool listed above to enable syntax checking.", None, "CodeMedic"
        ))
    return findings, syntax_ok, "\n".join(messages)

def format_python(code):
    try:
        result = subprocess.run(
            [os.sys.executable, "-m", "ruff", "format", "--stdin-filename", "reviewed_code.py", "-"],
            input=code, capture_output=True, text=True, timeout=12, shell=False
        )
        if result.returncode == 0:
            return result.stdout, ""
        return None, result.stderr or result.stdout or "Ruff could not format the code."
    except FileNotFoundError:
        return None, "Ruff is not installed. Run: python -m pip install ruff"
    except subprocess.TimeoutExpired:
        return None, "Formatting timed out."

with st.sidebar:
    st.markdown("## 🩺 CodeMedic AI")
    st.caption("Multi-language code review")
    language = st.selectbox("Choose programming language", list(SAMPLES))
    st.markdown("---")
    st.markdown("### Checks included")
    st.write("• Syntax / compile checks")
    st.write("• Selected quality hints")
    st.write("• Python AST + optional Ruff")
    st.write("• Downloadable JSON report")
    st.caption("Submitted programs are not run.")

if "current_language" not in st.session_state:
    st.session_state.current_language = language
if st.session_state.current_language != language:
    st.session_state.code_input = SAMPLES[language]
    st.session_state.current_language = language

st.markdown(
    '<div class="hero"><h1>🩺 CodeMedic AI</h1><p>Multi-Language Code Review · Syntax Checking · Quality Hints · Reports</p></div>',
    unsafe_allow_html=True
)
st.info("Educational static-analysis prototype. It checks submitted code without executing it. Static checks cannot guarantee code is bug-free or secure.")

left, right = st.columns([1.35, 0.65], gap="large")
with left:
    st.subheader(f"💻 {language} Code Editor")
    code = st.text_area("Paste your code here", key="code_input", height=380, label_visibility="collapsed")
    analyze_clicked = st.button("🔍 Analyze Code", type="primary", use_container_width=True)
with right:
    st.subheader("📌 How to use")
    st.markdown("1. Select a language.\\n2. Paste your code.\\n3. Click **Analyze Code**.\\n4. Review findings and download your report.")
    st.subheader("🧰 Tool requirements")
    st.caption("Python works with Python installed. Java needs a JDK, C needs GCC/Clang, C++ needs G++/Clang++, and JavaScript needs Node.js.")

if analyze_clicked:
    if not code.strip():
        st.warning("Please paste some code first.")
        st.session_state.pop("last_report", None)
    else:
        with st.spinner(f"Analyzing {language} code..."):
            if language == "Python":
                findings, syntax_ok, tool_message = analyze_python(code)
            else:
                findings, syntax_ok, tool_message = analyze_other(language, code)
        st.session_state.last_report = {
            "language": language, "findings": findings, "syntax_ok": syntax_ok,
            "tool_message": tool_message, "code": code
        }

if "last_report" in st.session_state:
    report = st.session_state.last_report
    findings = report["findings"]
    errors = sum(f["severity"] == "Error" for f in findings)
    warnings = sum(f["severity"] == "Warning" for f in findings)
    info_count = sum(f["severity"] == "Info" for f in findings)
    successes = sum(f["severity"] == "Success" for f in findings)
    score = max(0, 100 - errors * 25 - warnings * 10 - info_count * 3)

    st.markdown("---")
    st.subheader("📊 Analysis Dashboard")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Heuristic score", f"{score}/100")
    c2.metric("Errors", errors)
    c3.metric("Warnings", warnings)
    c4.metric("Info / Success", info_count + successes)
    st.caption("The score is a demo indicator, not a validated code quality or security metric.")

    if report["syntax_ok"] is True:
        st.success("The selected syntax/compile check passed.")
    elif report["syntax_ok"] is False:
        st.error("The selected syntax/compile check found an error.")
    else:
        st.warning("No syntax result is available. Read the tool status below.")

    st.subheader("🧾 Findings and explanations")
    for number, result in enumerate(findings, start=1):
        line = f" · Line {result['line']}" if result.get("line") else ""
        heading = f"{number}. [{result['severity']}] {result['title']}{line}"
        with st.expander(heading, expanded=result["severity"] in ("Error", "Warning")):
            st.write(result["message"])
            st.caption("Checker: " + result["source"])

    if report["tool_message"]:
        st.subheader("🧰 Tool status")
        st.warning(report["tool_message"])

    report_data = {
        "project": "CodeMedic AI", "language": report["language"],
        "heuristic_score": score,
        "score_note": "Demo indicator only; not a validated quality or security metric.",
        "syntax_check_passed": report["syntax_ok"], "findings": findings,
        "submitted_code": report["code"], "tool_message": report["tool_message"],
        "submitted_program_executed": False
    }
    st.download_button(
        "⬇️ Download JSON Report", json.dumps(report_data, indent=2),
        file_name=f"codemedic_{report['language'].lower().replace('+', 'p')}_report.json",
        mime="application/json"
    )

    if report["language"] == "Python":
        st.markdown("---")
        st.subheader("✨ Python Formatter")
        if st.button("Format Python Code"):
            formatted, error = format_python(report["code"])
            if error:
                st.error(error)
            else:
                st.code(formatted, language="python")
                st.download_button(
                    "⬇️ Download Formatted Python", formatted,
                    file_name="formatted_code.py", mime="text/x-python"
                )
                diff = "\n".join(difflib.unified_diff(
                    report["code"].splitlines(), formatted.splitlines(),
                    fromfile="before.py", tofile="after.py", lineterm=""
                ))
                if diff:
                    st.subheader("🔁 Before / After Diff")
                    st.code(diff, language="diff")
                else:
                    st.success("The code is already formatted.")

st.markdown("---")
st.caption("Built with Python and Streamlit. Compiler tools are optional local dependencies. This educational prototype does not replace professional code review or a security audit.")
