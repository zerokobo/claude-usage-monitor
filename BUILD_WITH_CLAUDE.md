# Claude Code に任せてインストールする

Windows 11 の PowerShell で Claude Code を起動し、下の枠の中をそのまま貼り付けてください。Claude Code がビルドから起動まで進めます。途中でコマンドの実行の許可を求められたら、内容を見て許可してください。

```text
Windows 11 に「Claude Usage Monitor」をインストールしてください。
ソースは https://github.com/zerokobo/claude-usage-monitor です（git がなければ Code → Download ZIP）。

手順:
1. ソースを %USERPROFILE%\src\claude-usage-monitor に用意する（git clone か、zip の展開）。
   README.md を最初に読み、書かれている仕組みと注意を確認する。
2. usage.py と app.py を読み、通信先が https://api.anthropic.com/api/oauth/usage だけであること、
   .credentials.json を読むだけで書き換えないことを確かめて、私に一言で報告する。
3. Python 3.12 が `py -3.12 --version` で使えるか確認する。なければ
   `winget install Python.Python.3.12` を実行してよいか私に聞いてから入れる。
4. `powershell -ExecutionPolicy Bypass -File .\build.ps1 -Install` を実行する。
   テストかビルドが失敗したら、エラーを読んで原因を説明し、直し方を提案する（勝手にテストを消さない）。
5. 起動したら、パネルに 5時間枠と週次枠の残りが出ているかを私に確認してもらう。
   Claude Code の /usage と同じ値になっているはず。
6. 自動起動を有効にするか私に聞く。有効にするなら、パネルの「設定」→「Windowsログイン時に起動」で行うよう案内する。

守ること:
- %USERPROFILE%\.claude\.credentials.json の中身を表示・コピー・送信しない。
- 私の許可なしに、このアプリ以外のファイルを消したり設定を変えたりしない。
```

## うまくいかないとき

| 症状 | 原因と対処 |
|---|---|
| 「ログイン情報がありません」 | Claude Code でログインしていない、または設定フォルダが違う。`claude` を起動してログインする。`CLAUDE_CONFIG_DIR` を使っているなら「アカウントを追加…」でそのフォルダを選ぶ |
| 「ログイン期限切れ」 | Claude Code をしばらく使っていない。Claude Code で何か1回やり取りすると更新される |
| 「取得間隔を空けています」 | 取得先に断られた。自動で間隔を広げて再試行する。同じアカウントの残量を取る別のツールを動かしていると起きやすい |
| 「HTTP 4xx / 5xx」が続く | 取得先の仕様が変わった可能性がある（公開 API ではないため）。リポジトリの更新を確認する |
| PowerShell で build.ps1 が実行できない | `powershell -ExecutionPolicy Bypass -File .\build.ps1 -Install` の形で実行する |
