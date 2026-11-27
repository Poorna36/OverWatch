import React, { useState } from 'react'
import { ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native'
import { Feather } from '@expo/vector-icons'
import { SafeAreaView } from 'react-native-safe-area-context'
import { useStore, type Tier } from '../lib/store'
import { BrandHeader, Btn, C, Card } from '../components/ui'

export function SettingsScreen({ navigation }: { navigation: any }) {
  const { tier, setTier } = useStore()
  const [google, setGoogle] = useState(false)
  const [serverUrl, setServerUrl] = useState('')
  const [token, setToken] = useState('')

  return (
    <SafeAreaView style={st.safe} edges={['top', 'left', 'right']}>
      <BrandHeader onPressBrand={() => navigation.navigate('SessionsTab')} />
      <ScrollView contentContainerStyle={st.content} showsVerticalScrollIndicator={false}>
        <Text style={st.h1}>Settings</Text>

        <Card style={{ gap: 14 }}>
          <Text style={st.cardTitle}>Control plane</Text>

          <View style={{ gap: 6 }}>
            <Text style={st.fieldLabel}>Server URL</Text>
            <TextInput
              value={serverUrl}
              onChangeText={setServerUrl}
              placeholder="https://overwatch.example.com"
              placeholderTextColor={C.mutedFg}
              autoCapitalize="none"
              autoCorrect={false}
              style={st.input}
            />
          </View>

          <View style={{ gap: 6 }}>
            <Text style={st.fieldLabel}>Session token</Text>
            <TextInput
              value={token}
              onChangeText={setToken}
              placeholder="••••••••"
              placeholderTextColor={C.mutedFg}
              secureTextEntry
              autoCapitalize="none"
              autoCorrect={false}
              style={st.input}
            />
          </View>

          <View style={st.lockRow}>
            <Feather name="lock" size={14} color={C.mutedFg} style={{ marginTop: 2 }} />
            <Text style={st.lockText}>
              Saved locally on this device.
            </Text>
          </View>
        </Card>

        <Card>
          <Text style={st.cardTitle}>Bandwidth preference</Text>
          <Text style={st.sub}>Default live-view tier for sessions.</Text>
          <View style={st.tierRow}>
            {(['video', 'screenshot', 'text'] as Tier[]).map((t) => {
              const active = tier === t
              return (
                <TouchableOpacity
                  key={t}
                  onPress={() => setTier(t)}
                  style={[st.tierBtn, active && st.tierBtnActive]}
                >
                  <Text style={[st.tierText, active && { color: C.foreground }]}>{t}</Text>
                </TouchableOpacity>
              )
            })}
          </View>
        </Card>

        <Card style={st.googleCard}>
          <View style={{ flex: 1 }}>
            <Text style={st.cardTitle}>Google connector</Text>
            <Text style={st.sub}>
              {google
                ? 'Connected · gmail.list, calendar.read'
                : 'Used by the Daily routine profile.'}
            </Text>
          </View>
          <Btn variant={google ? 'danger' : 'secondary'} onPress={() => setGoogle(!google)}>
            {google ? 'Disconnect' : 'Connect'}
          </Btn>
        </Card>
      </ScrollView>
    </SafeAreaView>
  )
}

const st = StyleSheet.create({
  safe: { flex: 1, backgroundColor: C.background },
  content: { padding: 16, paddingBottom: 32, gap: 16 },
  h1: { color: C.foreground, fontSize: 24, fontWeight: '700', letterSpacing: -0.4 },
  cardHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  cardTitle: { color: C.foreground, fontSize: 15, fontWeight: '700' },
  sub: { color: C.mutedFg, fontSize: 12, marginTop: 3 },
  fieldLabel: { color: C.foreground, fontSize: 13, fontWeight: '500' },
  input: {
    minHeight: 44,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: C.border,
    backgroundColor: C.surface,
    color: C.foreground,
    paddingHorizontal: 12,
    fontFamily: C.mono,
    fontSize: 13,
  },
  lockRow: { flexDirection: 'row', gap: 8 },
  lockText: { flex: 1, color: C.mutedFg, fontSize: 12, lineHeight: 17 },
  tierRow: { flexDirection: 'row', gap: 8, marginTop: 12 },
  tierBtn: {
    flex: 1,
    minHeight: 44,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: C.border,
    alignItems: 'center',
    justifyContent: 'center',
  },
  tierBtnActive: { borderColor: C.primary, backgroundColor: C.accent },
  tierText: { color: C.mutedFg, fontSize: 13, textTransform: 'capitalize', fontWeight: '600' },
  googleCard: { flexDirection: 'row', alignItems: 'center', gap: 12 },
})
