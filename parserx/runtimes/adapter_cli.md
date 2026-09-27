## 工作目录与调用方式

你在一个独立的工作目录里工作。目录里只有：工具入口 `./px`、配置 `parserx.yaml`（固定，不要修改）、三份方法说明 `skills/`（内容已附在本文末尾，不必再读文件）、本文件，以及文档 `{{input_name}}` 的工作区 `ws/`。

工具在命令行上调用：`./px tool <工具名> --ws ws [参数] --json`。参数的名字与下文"工具参考"相同，写作 `--名字 值`：列表用逗号分隔（`--kinds a,b`），几个数依次写（`--bbox 72 200 300 214`），true / false 的参数写上即为 true（`--full`）；`looks`、`ops` 是 JSON 列表，从标准输入给出（`--looks -`、`--ops -`，用 heredoc）。只看一处原件时，这一处的字段可以直接写成参数（`--block b-p004-0002 --as answer --question "……"`）。`--request -` 从标准输入给出整个请求的 JSON。`./px tool <工具名> --help` 列出参数。

```
./px tool read_draft --ws ws --view issues --json
./px tool read_draft --ws ws --view text --start b-p003-0012 --after 40 --json
./px tool view_source --ws ws --looks - --json <<'EOF'
[{"block": "b-p004-0002", "as": "answer", "question": "第 1 行第 2 列写的是什么？"}]
EOF
./px tool edit_draft --ws ws --ops - --json <<'EOF'
[{"op": "set_role", "block": "b-p002-0003", "role": "H2", "reason": "……"}]
EOF
./px tool submit_draft --ws ws --json
```

在这个目录里：

- 可以写只读的分析脚本（用 `./px python 脚本.py` 运行，也可以把工具的 JSON 输出交给脚本分析）。
- 不要直接写 `ws/` 下的任何文件，也不要在脚本里调用工作区的写入接口：这类改动会被程序检查出来，你的全部修改都不会被采用。不要直接读 `ws/` 下的内部文件，用 `read_draft`。
- 只使用本目录里的文件（系统临时目录除外），不要读取本目录以外的路径。
