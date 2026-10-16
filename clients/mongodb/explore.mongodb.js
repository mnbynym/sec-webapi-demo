// MongoDB for VS Code の Playground
// 確認したい文を選択して、右上の実行ボタン（▷）で実行する。解説は docs/mongodb-data.md
use('webapi_demo');

// ---------------------------------------------------------------- 1. データの中身
db.products.findOne({ sku: 'SKU-000001' });
db.products.countDocuments();
db.products.aggregate([{ $group: { _id: '$category', count: { $sum: 1 } } }, { $sort: { _id: 1 } }]);

// ---------------------------------------------------------------- 2. インデックス
db.products.getIndexes();

// ---------------------------------------------------------------- 3. 2-3 ESR インデックスの実行計画
// hint を 'ers_category_price_created' に変えて比較する
db.products
  .find({ category: 'books', price: { $gte: 1000, $lte: 5000 } })
  .sort({ created_at: -1, _id: -1 })
  .limit(20)
  .hint('esr_category_created_price')
  .explain('executionStats');

// ---------------------------------------------------------------- 4. 4-3 楽観的ロック（version の確認）
db.products.find({ sku: 'SKU-000005' }, { _id: 0, sku: 1, price: 1, version: 1 });

// ---------------------------------------------------------------- 5. 4-2 冪等コンシューマの結果（make relay 実行後）
db.order_events.find().limit(5);
db.sales_by_sku.find().sort({ quantity: -1 }).limit(5);
