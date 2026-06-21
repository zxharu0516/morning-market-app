# iPhone / Android アプリ化スターター

この `mobile-app` は、既存のFastAPI版アプリをスマホアプリ内のWebViewで表示するためのExpoプロジェクトです。

## 重要

App Store公開時は、`http://127.0.0.1:8000` では動きません。
スマホアプリからアクセスできる `https://` の公開サーバーURLが必要です。

例：

```env
EXPO_PUBLIC_WEBAPP_URL=https://your-domain.example.com
```

## ローカルで実機テストする方法

1. PCでバックエンドを起動

```powershell
cd "C:\Users\はるき\Desktop\morning-market-column-ai-pro-v26-mobile-app"
python -m pip install -r requirements.txt
Copy-Item .env.example .env -Force
python -m uvicorn app.main:app --host 0.0.0.0 --reload
```

2. PCのローカルIPを確認

```powershell
ipconfig
```

例：`192.168.1.23`

3. `mobile-app/.env` を作る

```powershell
cd mobile-app
Copy-Item .env.example .env -Force
notepad .env
```

中身をこのように変更：

```env
EXPO_PUBLIC_WEBAPP_URL=http://192.168.1.23:8000
```

4. Expoを起動

```powershell
npm install
npx expo start
```

スマホにExpo Goを入れて、QRコードを読み取るとテストできます。

## App Store用にビルドする方法

Apple Developer Program登録後、以下を実行します。

```powershell
cd mobile-app
npm install
npx eas login
npx eas build:configure
npx eas build -p ios --profile production
```

ビルド後、App Store Connectへ提出：

```powershell
npx eas submit -p ios
```

## 変更する場所

- アプリ名: `mobile-app/app.json` の `expo.name`
- Bundle ID: `mobile-app/app.json` の `ios.bundleIdentifier`
- Android Package: `mobile-app/app.json` の `android.package`
- 表示URL: `mobile-app/.env` の `EXPO_PUBLIC_WEBAPP_URL`
- アイコン: `mobile-app/assets/icon.png`
- スプラッシュ: `mobile-app/assets/splash.png`
