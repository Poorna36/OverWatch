import React, { useEffect, useState } from 'react'
import { Modal, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native'
import { Feather } from '@expo/vector-icons'
import { REASONS, type ToolCall } from '../lib/mock'
import { Btn, C, RiskBadge } from './ui'

export function ApprovalSheet({
  call,
  onDecide,
  onClose,
}: {
  call: ToolCall
  onDecide: (approve: boolean, expired?: boolean) => void
  onClose: () => void
}) {
  const [left, setLeft] = useState(90)

  useEffect(() => {
    const t = setInterval(() => setLeft((l) => l - 1), 1000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    if (left <= 0) onDecide(false, true)
  }, [left, onDecide])

  const mins = Math.floor(Math.max(0, left) / 60)
  const secs = String(Math.max(0, left) % 60).padStart(2, '0')

  return (
    <Modal visible transparent animationType="slide" onRequestClose={onClose}>
      <View style={s.backdrop}>
        <TouchableOpacity style={StyleSheet.absoluteFill} activeOpacity={1} onPress={onClose} />
        <View style={s.sheet}>
          <View style={s.handle} />
          <ScrollView showsVerticalScrollIndicator={false}>
            <View style={s.header}>
              <View style={s.iconBox}>
                <Feather name="alert-triangle" size={20} color={C.ask} />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={s.eyebrow}>Approval required · {call.reason}</Text>
                <Text style={s.tool} numberOfLines={1}>{call.tool}</Text>
              </View>
              <TouchableOpacity onPress={onClose} style={s.closeBtn}>
                <Feather name="x" size={20} color={C.mutedFg} />
              </TouchableOpacity>
            </View>

            <Text style={s.reasonDesc}>{REASONS[call.reason]}</Text>

            <View style={s.argsBox}>
              <Text style={s.argsTitle}>SANITIZED ARGUMENTS</Text>
              <View style={{ gap: 4 }}>
                {Object.entries(call.args).map(([k, v]) => (
                  <View key={k} style={s.argRow}>
                    <Text style={s.argKey}>{k}:</Text>
                    <Text style={s.argVal}>{v}</Text>
                  </View>
                ))}
              </View>
            </View>

            <View style={s.grid2}>
              <View style={s.statCard}>
                <Text style={s.statLabel}>Risk tier</Text>
                <RiskBadge r={call.risk} />
              </View>
              <View style={s.statCard}>
                <Text style={s.statLabel}>Expires in</Text>
                <Text style={[s.countdown, { color: left < 20 ? C.block : C.ask }]}>
                  {mins}:{secs}
                </Text>
              </View>
            </View>

            {call.reversible ? (
              <View style={s.revRow}>
                <Feather name="rotate-ccw" size={15} color={C.allow} />
                <Text style={s.revText}>Reversible — can be rolled back in the workspace.</Text>
              </View>
            ) : (
              <View style={s.warnBox}>
                <Feather name="alert-triangle" size={16} color={C.block} style={{ marginTop: 2 }} />
                <Text style={s.warnText}>
                  <Text style={{ fontWeight: '700', color: C.block }}>Cannot be undone. </Text>
                  Once uploaded, the file leaves the sandbox and OverWatch cannot recall it.
                </Text>
              </View>
            )}

            <Text style={s.hint}>If the timer runs out, the request is denied automatically.</Text>

            <View style={s.actions}>
              <Btn variant="danger" style={{ flex: 1, minHeight: 48 }} onPress={() => onDecide(false)}>
                Deny
              </Btn>
              <Btn variant="approve" style={{ flex: 1, minHeight: 48 }} onPress={() => onDecide(true)}>
                Approve
              </Btn>
            </View>
          </ScrollView>
        </View>
      </View>
    </Modal>
  )
}

const s = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(17, 18, 24, 0.72)',
    justifyContent: 'flex-end',
  },
  sheet: {
    maxHeight: '90%',
    backgroundColor: C.popover,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    borderWidth: 1,
    borderColor: C.border,
    padding: 20,
    paddingBottom: 28,
  },
  handle: {
    alignSelf: 'center',
    width: 40,
    height: 4,
    borderRadius: 2,
    backgroundColor: C.border,
    marginBottom: 16,
  },
  header: { flexDirection: 'row', alignItems: 'flex-start', gap: 12 },
  iconBox: {
    width: 40,
    height: 40,
    borderRadius: 10,
    backgroundColor: C.ask + '24',
    alignItems: 'center',
    justifyContent: 'center',
  },
  eyebrow: { color: C.mutedFg, fontSize: 12 },
  tool: { color: C.foreground, fontSize: 18, fontFamily: C.mono, fontWeight: '700', marginTop: 2 },
  closeBtn: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center' },
  reasonDesc: { color: C.mutedFg, fontSize: 13, marginTop: 12, lineHeight: 19 },
  argsBox: {
    marginTop: 14,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: C.border,
    backgroundColor: C.surface,
    padding: 12,
  },
  argsTitle: { color: C.mutedFg, fontSize: 10, letterSpacing: 1, marginBottom: 8 },
  argRow: { flexDirection: 'row', gap: 6 },
  argKey: { color: C.mutedFg, fontSize: 12, fontFamily: C.mono },
  argVal: { flex: 1, color: C.foreground, fontSize: 12, fontFamily: C.mono },
  grid2: { flexDirection: 'row', gap: 10, marginTop: 12 },
  statCard: {
    flex: 1,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: C.border,
    padding: 12,
    gap: 4,
  },
  statLabel: { color: C.mutedFg, fontSize: 11 },
  countdown: { fontFamily: C.mono, fontWeight: '700', fontSize: 15 },
  revRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 12 },
  revText: { color: C.allow, fontSize: 13, flex: 1 },
  warnBox: {
    flexDirection: 'row',
    gap: 10,
    marginTop: 12,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: C.block + '66',
    backgroundColor: C.block + '18',
    padding: 12,
  },
  warnText: { flex: 1, color: C.foreground, fontSize: 13, lineHeight: 18 },
  hint: { color: C.mutedFg, fontSize: 12, marginTop: 12 },
  actions: { flexDirection: 'row', gap: 10, marginTop: 16 },
})
