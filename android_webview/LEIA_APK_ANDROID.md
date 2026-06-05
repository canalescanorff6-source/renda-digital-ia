# Gerar APK Android

Este projeto é um aplicativo Android WebView que abre a versão online do sistema.

Antes de gerar o APK:

1. Coloque o sistema online no RunSite.
2. Abra `android_webview/app/src/main/res/values/strings.xml`.
3. Troque:

```xml
<string name="app_url">https://SEU-SITE-RUNSITE.runsite.app</string>
```

pela URL real do seu sistema.

Depois:

1. Abra a pasta `android_webview` no Android Studio.
2. Espere sincronizar o Gradle.
3. Clique em **Build > Build Bundle(s) / APK(s) > Build APK(s)**.
4. O APK será gerado pelo Android Studio.

Observação: o APK depende do site online. Ele não roda o backend Flask dentro do celular; ele abre a versão hospedada com login, produtos, vendas, leads e vídeos.
