6 件とも採点しました。基準は採点観点の T2（K1〜K5）です。回答中の具体的な主張はソースで確認しました。

**主張が正しかったもの**
- `resolveControllerRef` は 6 か所にあり、行番号もすべて合っていました。
- `RecheckDeletionTimestamp` に渡すクロージャも 6 か所で、行番号も合っています。
- `processNextWorkItem` は 27 ファイルにあります。
- `DeletedFinalStateUnknown` を含む本番ファイルは 31 個です。
- `controller_ref_manager.go` の Claim / Adopt / Release の重複も主張どおりでした。
- `printDiff` と `usagesToSet` は、2 か所で中身がまったく同じでした。
- `uidCache` の 2 ファイルの違いは、メソッド名の大文字・小文字だけでした。
- `SetDeploymentCondition` だけが `LastTransitionTime` を前の値のまま残す、という点も確かです。
- `recordMetrics` は、一方が `errors.As`、もう一方が型アサーションでした。
- `isPodTerminated` は、未知の phase の扱いが違いました。

**誤りが見つかったもの**
- Answer 2: tombstone 処理を「16 ファイル・25 か所」としていますが、実際は 31 ファイル・40 か所強です。
- Answer 5: ReplicaSet のエラーメッセージは `"sync %q failed with %v"` です。「`"%v failed with : %v"` まで実質コピペ」という主張は誤りです。Deployment（:486）と Endpoints（:312）の `handleErr` の行番号は、実際には `processNextWorkItem` の位置を指していました。

**全回答に共通する抜け**
- K3（Ready になったときの minReadySeconds 後の再キュー）と K4（Pod の並び替えの `Less` の重複）は、どの回答も挙げていません。

Answer 3・4・6 は、指摘がどれもソースで確認でき、正確で丁寧です。ただし扱っているのは主に同名関数の重複で、正解の要点（ワーカーループ、`resolveControllerRef`、イベントハンドラーと enqueue）をほとんど押さえていないため、低めの点にしています。

```json
[
  {"answer": 1, "score": 4, "comment": "K1（processNextWorkItem 27 ファイル、worker）と K2（resolveControllerRef 6 か所、tombstone → resolve → enqueue、enqueue 系）を正確な行番号付きで挙げ、ControllerRefManager の内部の重複や Deployment の handleErr の違いなどのリスクにも触れています（K5 の一部）。K3（minReadySeconds の再キュー）と K4（Pod の並び替え）が抜けています。"},
  {"answer": 2, "score": 3, "comment": "resolveControllerRef 6 か所（K2）と、Condition ヘルパーの重複（LastTransitionTime の違いも正確）は良い指摘です。ワーカーループ（K1）は軽く触れて優先度を下げただけで、tombstone 処理を『16 ファイル・25 か所』としているのは実際（31 ファイル・40 か所強）より大幅に少なく、K3 と K4 も抜けています。"},
  {"answer": 3, "score": 2, "comment": "uidCache、printDiff、usagesToSet、getPatch、recordMetrics、isPodTerminated など、同名関数の重複はどれも正確で妥当です。ただしワーカーループ、resolveControllerRef、イベントハンドラーと enqueue、minReadySeconds の再キュー、Pod の並び替えといった正解の要点をほぼ押さえておらず、要点の一部しかありません。"},
  {"answer": 4, "score": 2, "comment": "uidCache、printDiff、usagesToSet、slowStartBatch の違いの表、recordMetrics の errors.As の違い、replication の Condition 関数が本番で使われていない点など、正確で丁寧です。それでも K1（ワーカーループ）、K2（resolveControllerRef と enqueue）、K3、K4 という正解の要点は抜けています。"},
  {"answer": 5, "score": 4, "comment": "tombstone 処理、processNextWorkItem 27 か所と ErrorPolicy による共通化（K1）、resolveControllerRef と addPod / updatePod / deletePod の骨格（K2）、RecheckDeletionTimestamp、expectations の違いによるリスク（K5）を押さえています。ただし RS のエラーメッセージが同じとした点は誤りで（実際は \"sync %q failed with %v\"）、handleErr の行番号もずれています。K3 と K4 は抜けています。"},
  {"answer": 6, "score": 3, "comment": "Condition、slowStartBatch、printDiff、usagesToSet、uidCache、tombstone 処理、recordMetrics など、正確な同名重複の指摘が多いです。processNextWorkItem 27 か所（K1）には触れていますが、優先度が低いとして提案はしていません。resolveControllerRef と enqueue（K2）、K3、K4 は抜けています。"}
]
```