# このリポジトリでのコードの調べ方

codebase-memory-mcp（コードのグラフ）を入れてあり、インデックスは作成済み。

- コードの構造を調べるとき（関数の呼び出し元・呼び出し先、処理の流れ、変更の影響範囲、未使用コード、インターフェースの実装の一覧など）は、grep より先に codebase-memory-mcp の MCP ツール（search_graph、trace_path、query_graph、get_code_snippet など）を使うこと。
- 文字列や設定ファイルを探すとき、グラフの結果を裏取りするときは、grep や Read を使ってよい。
- インデックスは作成済みなので、index_repository は呼ばないこと。
