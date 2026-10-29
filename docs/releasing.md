# 发布到 PyPI

维护者使用 GitHub Actions 的手动发布工作流。普通 push 或 pull request 只运行测试，
不会发布。工作流使用 PyPI Trusted Publishing，不需要把长期 PyPI token 放进仓库。

## 首次配置

在 GitHub 仓库 `gosgjkaj/hygonstat` 创建名为 `pypi` 的 environment，限制为
`main` 分支。当前发布由维护者手动触发，必须显式选择 `publish=true`。
如团队需要独立审批，可额外配置 required reviewers；这不是当前流程的必需步骤。

在 [PyPI Publishing](https://pypi.org/manage/account/publishing/) 创建 pending publisher：

| 字段 | 值 |
| --- | --- |
| PyPI project name | `hygonstat` |
| GitHub owner | `gosgjkaj` |
| Repository name | `hygonstat` |
| Workflow filename | `publish.yml` |
| Environment name | `pypi` |

首次成功上传后，pending publisher 会转成项目的正常 publisher。提前添加 publisher
不会预留项目名；实际上传成功前需确认名称仍可使用。

如果在 fork 中维护另一个发行包，需要使用自己的 PyPI 项目名、publisher 和仓库信息，
并修改工作流中的仓库校验及项目元数据。

## 每次发布

1. 更新 `pyproject.toml` 和 `hygonstat/__init__.py` 中的版本，确保二者一致。
2. 合并到 `main`，确认 Tests 工作流通过。
3. 在 Actions 选择 **Build and publish to PyPI**，从 `main` 手动运行，填写版本，
   保持 `publish=false`。此运行只构建、验证和上传工作流 artifact。
4. 检查 `python-package-distributions` artifact、README、metadata 和测试结果。
5. 准备正式发布时，再从 `main` 运行同一工作流，填写相同版本并选择 `publish=true`。
   触发前核对 commit 与已检查的构建来源一致；若配置了 environment reviewer，需完成相应审批。
   发布 job 只下载并上传本次已经验证的 artifact，不重新构建。
6. 在 PyPI 确认版本可见，并在干净环境中执行 `pip install hygonstat==版本号`
   和 `hygonstat --help`。需要实际查询设备时仍须 Linux 和海光驱动。

PyPI 已上传的同名同版本文件不能覆盖。发现发布内容错误时，应修复并增加版本号。

## 本地验证

```bash
python -m venv .venv
.venv/bin/python -m pip install build twine .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m build
.venv/bin/python -m twine check --strict dist/*
```

源码包包含测试、脱敏样本和系统安装脚本；wheel 包含运行模块和发行元数据。
CLI 的 `--help` 与 `--version` 不要求 GPU，实际采集必须在支持的 Linux 主机上验证。

参考：[PyPI 首次发布](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)、
[PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/)、
[PyPA GitHub Actions 发布指南](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/)。
