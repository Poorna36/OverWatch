import React, { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { Animated, StyleSheet, Text, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { C } from '../components/ui'

type Notice = { message: string; description?: string }

const ToastCtx = createContext<(message: string, options?: { description?: string }) => void>(() => {})

export function ToastProvider({ children }: { children: ReactNode }) {
  const [notice, setNotice] = useState<Notice | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const opacity = useRef(new Animated.Value(0)).current
  const insets = useSafeAreaInsets()

  const toast = useCallback((message: string, options?: { description?: string }) => {
    if (timerRef.current) clearTimeout(timerRef.current)
    setNotice({ message, description: options?.description })
    opacity.setValue(0)
    Animated.timing(opacity, { toValue: 1, duration: 180, useNativeDriver: true }).start()
    timerRef.current = setTimeout(() => {
      Animated.timing(opacity, { toValue: 0, duration: 180, useNativeDriver: true }).start(() => {
        setNotice(null)
      })
    }, 3000)
  }, [opacity])

  useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current)
  }, [])

  return (
    <ToastCtx.Provider value={toast}>
      {children}
      {notice && (
        <Animated.View
          pointerEvents="none"
          style={[st.wrap, { top: insets.top + 56, opacity }]}
        >
          <View style={st.box}>
            <Text style={st.title}>{notice.message}</Text>
            {notice.description ? <Text style={st.desc}>{notice.description}</Text> : null}
          </View>
        </Animated.View>
      )}
    </ToastCtx.Provider>
  )
}

export function useToast() {
  return useContext(ToastCtx)
}

const st = StyleSheet.create({
  wrap: {
    position: 'absolute',
    left: 16,
    right: 16,
    zIndex: 99,
    alignItems: 'center',
  },
  box: {
    width: '100%',
    maxWidth: 380,
    backgroundColor: C.popover,
    borderWidth: 1,
    borderColor: C.border,
    borderRadius: 12,
    padding: 14,
    shadowColor: '#000',
    shadowOpacity: 0.45,
    shadowRadius: 16,
    shadowOffset: { width: 0, height: 8 },
    elevation: 10,
  },
  title: { color: C.foreground, fontSize: 13, fontWeight: '700' },
  desc: { color: C.mutedFg, fontSize: 12, marginTop: 3, lineHeight: 17 },
})
