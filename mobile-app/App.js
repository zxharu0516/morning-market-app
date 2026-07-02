import React, { useMemo, useState } from 'react';
import { ActivityIndicator, Linking, Platform, SafeAreaView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import Constants from 'expo-constants';
import { StatusBar } from 'expo-status-bar';
import { WebView } from 'react-native-webview';

const DEFAULT_URL = 'https://morning-market-app.onrender.com';

function normalizeUrl(url) {
  if (!url) return DEFAULT_URL;
  const trimmed = String(url).trim();
  if (!trimmed) return DEFAULT_URL;
  return trimmed.endsWith('/') ? trimmed.slice(0, -1) : trimmed;
}

export default function App() {
  const webAppUrl = useMemo(() => {
    return normalizeUrl(
      process.env.EXPO_PUBLIC_WEBAPP_URL ||
      Constants?.expoConfig?.extra?.webAppUrl ||
      DEFAULT_URL
    );
  }, []);

  const [webKey, setWebKey] = useState(1);
  const [hasError, setHasError] = useState(false);

  const canOpenInsideApp = (url) => {
    if (!url) return true;
    return url.startsWith(webAppUrl) || url.startsWith('about:blank');
  };

  if (hasError) {
    return (
      <SafeAreaView style={styles.safeArea}>
        <StatusBar style="light" />
        <View style={styles.errorContainer}>
          <Text style={styles.title}>今日のマーケット</Text>
          <Text style={styles.message}>アプリに接続できませんでした。</Text>
          <Text style={styles.hint}>サーバーURLを確認してください。</Text>
          <Text style={styles.url}>{webAppUrl}</Text>
          <TouchableOpacity
            style={styles.button}
            onPress={() => {
              setHasError(false);
              setWebKey((value) => value + 1);
            }}
          >
            <Text style={styles.buttonText}>再読み込み</Text>
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar style="light" />
      <WebView
        key={webKey}
        source={{ uri: webAppUrl }}
        style={styles.webview}
        javaScriptEnabled
        domStorageEnabled
        sharedCookiesEnabled
        thirdPartyCookiesEnabled
        allowsInlineMediaPlayback
        mediaPlaybackRequiresUserAction={false}
        pullToRefreshEnabled={Platform.OS === 'android'}
        startInLoadingState
        renderLoading={() => (
          <View style={styles.loading}>
            <ActivityIndicator size="large" />
            <Text style={styles.loadingText}>読み込み中...</Text>
          </View>
        )}
        onError={(event) => {
          console.log('WebView error', event.nativeEvent);
        }}
        onHttpError={(event) => {
          console.log('WebView http error', event.nativeEvent);
        }}
        onShouldStartLoadWithRequest={(request) => {
          const url = request?.url || '';
          if (canOpenInsideApp(url)) return true;
          Linking.openURL(url).catch(() => {});
          return false;
        }}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: '#111827'
  },
  webview: {
    flex: 1,
    backgroundColor: '#f6f7fb'
  },
  loading: {
    position: 'absolute',
    top: 0,
    right: 0,
    bottom: 0,
    left: 0,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#f6f7fb'
  },
  loadingText: {
    marginTop: 12,
    fontSize: 15,
    color: '#111827',
    fontWeight: '700'
  },
  errorContainer: {
    flex: 1,
    padding: 24,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#111827'
  },
  title: {
    color: '#ffffff',
    fontSize: 28,
    fontWeight: '900',
    marginBottom: 18
  },
  message: {
    color: '#ffffff',
    fontSize: 18,
    fontWeight: '800',
    marginBottom: 8
  },
  hint: {
    color: '#cbd5e1',
    fontSize: 14,
    marginBottom: 12,
    textAlign: 'center'
  },
  url: {
    color: '#93c5fd',
    fontSize: 12,
    marginBottom: 22,
    textAlign: 'center'
  },
  button: {
    backgroundColor: '#ffffff',
    borderRadius: 16,
    paddingVertical: 14,
    paddingHorizontal: 24
  },
  buttonText: {
    color: '#111827',
    fontSize: 16,
    fontWeight: '900'
  }
});
