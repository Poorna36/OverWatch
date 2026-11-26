import React, { useState, useCallback } from 'react'
import { View, Text, StyleSheet } from 'react-native'
import { GestureHandlerRootView } from 'react-native-gesture-handler'
import { SafeAreaProvider } from 'react-native-safe-area-context'
import { NavigationContainer, DefaultTheme } from '@react-navigation/native'
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs'
import { createStackNavigator } from '@react-navigation/stack'
import { LoginScreen } from './screens/LoginScreen'
import { ChatScreen } from './screens/ChatScreen'
import { HomeScreen } from './screens/HomeScreen'
import { TasksScreen } from './screens/TasksScreen'
import { SettingsScreen } from './screens/SettingsScreen'
import { SessionScreen } from './screens/SessionScreen'
import {
  DEFAULT_ENV, INITIAL_SCOPE_TOOLS, MOCK_CHATS,
  type AgentRole, type ChatMessage, type ChatThread, type EnvConfig,
  type ScopeToolItem, type ToolCallEvent,
} from './lib/mock-data'
import { buildSimulation } from './lib/agent-sim'
import { T } from './components/ui'

export interface RunningChatState {
  statusLabel: string
  steps: ToolCallEvent[]
  previewTitle?: string
  previewLines?: string[]
}

const Tab = createBottomTabNavigator()
const Stack = createStackNavigator()

const Theme = {
  ...DefaultTheme,
  colors: {
    ...DefaultTheme.colors,
    background: T.bg,
    card: T.surface,
    text: T.text,
    border: T.border,
    primary: T.indigo,
    notification: T.indigo,
  },
}

function TabIcon({
  code, label, focused,
}: { code: 'chat' | 'home' | 'tools' | 'settings'; label: string; focused: boolean }) {
  const glyph = { chat: '>', home: '~', tools: '#', settings: '*' }[code]
  return (
    <View style={[ti.wrap, focused && ti.wrapFocused]}>
      <Text style={[ti.icon, focused && ti.iconFocused]}>{glyph}</Text>
      <Text style={[ti.label, focused && ti.labelFocused]}>{label}</Text>
    </View>
  )
}

const ti = StyleSheet.create({
  wrap: {
    alignItems: 'center', justifyContent: 'center',
    gap: 2, paddingHorizontal: 14, paddingVertical: 6,
    borderRadius: 12,
  },
  wrapFocused: { backgroundColor: T.indigoDim },
  icon: { fontSize: 15, color: T.textDim },
  iconFocused: { color: T.indigo },
  label: { fontSize: 10, color: T.textDim, fontWeight: '600', letterSpacing: 0.3 },
  labelFocused: { color: T.indigo },
})

function MainTabs({
  navigation,
  env,
  onChangeEnv,
  userEmail,
  onLogout,
  chats,
  onUpdateChats,
  activeChatId,
  onSelectChat,
  runningMap,
  onStartPrompt,
  scopeTools,
  onUpdateScopeTools,
}: {
  navigation: any
  env: EnvConfig
  onChangeEnv: (e: EnvConfig) => void
  userEmail: string
  onLogout: () => void
  chats: ChatThread[]
  onUpdateChats: React.Dispatch<React.SetStateAction<ChatThread[]>>
  activeChatId: string | null
  onSelectChat: (id: string | null) => void
  runningMap: Record<string, RunningChatState>
  onStartPrompt: (prompt: string, role: AgentRole, targetChatId: string | null) => void
  scopeTools: ScopeToolItem[]
  onUpdateScopeTools: React.Dispatch<React.SetStateAction<ScopeToolItem[]>>
}) {
  return (
    <Tab.Navigator
      initialRouteName="ChatTab"
      screenOptions={{
        headerShown: false,
        tabBarStyle: {
          backgroundColor: T.surface,
          borderTopWidth: 1,
          borderTopColor: T.border,
          height: 70,
          paddingBottom: 10,
          paddingTop: 8,
        },
        tabBarShowLabel: false,
      }}
    >
      {/* 1. Chat FIRST */}
      <Tab.Screen
        name="ChatTab"
        options={{
          tabBarIcon: ({ focused }) => <TabIcon code="chat" label="Chat" focused={focused} />,
        }}
      >
        {(props) => (
          <ChatScreen
            chats={chats}
            activeChatId={activeChatId}
            onSelectChat={onSelectChat}
            onUpdateChats={onUpdateChats}
            runningMap={runningMap}
            onStartPrompt={onStartPrompt}
            env={env}
            userEmail={userEmail}
            navigation={props.navigation}
          />
        )}
      </Tab.Screen>

      {/* 2. Home (Overview, Approvals, Running Tasks & Audit Logs) */}
      <Tab.Screen
        name="HomeTab"
        options={{
          tabBarIcon: ({ focused }) => <TabIcon code="home" label="Home" focused={focused} />,
        }}
      >
        {(props) => (
          <HomeScreen
            navigation={{
              ...props.navigation,
              navigate: (name: string, params?: any) => navigation.navigate(name, params),
            }}
            env={env}
            chats={chats}
            onOpenChat={(id) => {
              onSelectChat(id)
              props.navigation.navigate('ChatTab')
            }}
          />
        )}
      </Tab.Screen>

      {/* 3. Tools, Permissions & Image Scope */}
      <Tab.Screen
        name="TasksTab"
        options={{
          tabBarIcon: ({ focused }) => <TabIcon code="tools" label="Tools" focused={focused} />,
        }}
      >
        {() => (
          <TasksScreen
            scopeTools={scopeTools}
            onUpdateScopeTools={onUpdateScopeTools}
            env={env}
          />
        )}
      </Tab.Screen>

      {/* 4. Profile & Settings */}
      <Tab.Screen
        name="SettingsTab"
        options={{
          tabBarIcon: ({ focused }) => <TabIcon code="settings" label="Profile" focused={focused} />,
        }}
      >
        {() => (
          <SettingsScreen
            env={env}
            onChangeEnv={onChangeEnv}
            userEmail={userEmail}
            onLogout={onLogout}
          />
        )}
      </Tab.Screen>
    </Tab.Navigator>
  )
}

export default function App() {
  const [loggedIn, setLoggedIn] = useState(false)
  const [userEmail, setUserEmail] = useState('poorn@overwatch.dev')
  const [env, setEnv] = useState<EnvConfig>(DEFAULT_ENV)
  const [chats, setChats] = useState<ChatThread[]>(MOCK_CHATS)
  const [activeChatId, setActiveChatId] = useState<string | null>(null)
  const [runningMap, setRunningMap] = useState<Record<string, RunningChatState>>({})
  const [scopeTools, setScopeTools] = useState<ScopeToolItem[]>(INITIAL_SCOPE_TOOLS)

  // Concurrently runs a chat prompt in background (~11-12s) so user can switch chats freely
  const handleStartPrompt = useCallback(async (
    rawPrompt: string,
    role: AgentRole,
    existingChatId: string | null,
  ) => {
    const prompt = rawPrompt.trim()
    if (!prompt) return

    const gmailTool = scopeTools.find(t => t.id === 'gmail')
    const gmailBlocked = Boolean(gmailTool?.stopped || gmailTool?.permission === 'deny')
    const sim = buildSimulation(prompt, role, env, { gmailBlocked })

    const userMsg: ChatMessage = {
      id: `u_${Date.now()}`,
      sender: 'user',
      text: prompt,
      timestamp: new Date(),
    }

    const threadId = existingChatId ?? `chat_${Date.now()}`
    if (!existingChatId) {
      const newThread: ChatThread = {
        id: threadId,
        title: sim.title,
        role,
        updatedAt: new Date(),
        messages: [userMsg],
      }
      setChats(prev => [newThread, ...prev])
      setActiveChatId(threadId)
    } else {
      setChats(prev =>
        prev.map(c =>
          c.id === threadId
            ? { ...c, updatedAt: new Date(), messages: [...c.messages, userMsg] }
            : c
        )
      )
    }

    setRunningMap(prev => ({
      ...prev,
      [threadId]: { statusLabel: 'Working on it...', steps: [] },
    }))

    const collectedCalls: ToolCallEvent[] = []
    let lastPreview: { title: string; lines: string[] } | undefined

    for (let i = 0; i < sim.steps.length; i++) {
      const step = sim.steps[i]
      setRunningMap(prev => ({
        ...prev,
        [threadId]: {
          ...(prev[threadId] ?? { steps: [] }),
          statusLabel: step.statusLabel || 'Working on it...',
        },
      }))

      await new Promise(r => setTimeout(r, step.delayMs))

      if (step.previewTitle && step.previewLines) {
        lastPreview = { title: step.previewTitle, lines: step.previewLines }
      }
      if (step.toolCall) {
        const tc: ToolCallEvent = {
          ...step.toolCall,
          id: `tc_${Date.now()}_${i}`,
          seq: i + 1,
          timestamp: new Date(),
        }
        collectedCalls.push(tc)
      }

      setRunningMap(prev => ({
        ...prev,
        [threadId]: {
          statusLabel: step.statusLabel || 'Working on it...',
          steps: [...collectedCalls],
          previewTitle: lastPreview?.title,
          previewLines: lastPreview?.lines,
        },
      }))
    }

    const agentMsg: ChatMessage = {
      id: `a_${Date.now()}`,
      sender: 'agent',
      role,
      text: sim.finalText,
      timestamp: new Date(),
      toolCalls: collectedCalls,
      previewTitle: lastPreview?.title,
      previewLines: lastPreview?.lines,
      approval: sim.approval
        ? {
            ...sim.approval,
            id: `apr_${Date.now()}`,
            createdAt: new Date(),
            expiresAt: new Date(Date.now() + 120_000),
          }
        : undefined,
      approvalStatus: sim.approval ? 'pending' : undefined,
    }

    setChats(prev =>
      prev.map(c =>
        c.id === threadId
          ? { ...c, updatedAt: new Date(), messages: [...c.messages, agentMsg] }
          : c
      )
    )

    setRunningMap(prev => {
      const next = { ...prev }
      delete next[threadId]
      return next
    })
  }, [env, scopeTools])

  if (!loggedIn) {
    return (
      <GestureHandlerRootView style={{ flex: 1 }}>
        <SafeAreaProvider>
          <LoginScreen
            env={env}
            onChangeEnv={setEnv}
            onComplete={(email) => {
              setUserEmail(email)
              setLoggedIn(true)
            }}
          />
        </SafeAreaProvider>
      </GestureHandlerRootView>
    )
  }

  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <NavigationContainer theme={Theme}>
          <Stack.Navigator
            screenOptions={{
              headerShown: false,
              cardStyle: { backgroundColor: T.bg },
              gestureEnabled: true,
            }}
          >
            <Stack.Screen name="Main">
              {(props) => (
                <MainTabs
                  navigation={props.navigation}
                  env={env}
                  onChangeEnv={setEnv}
                  userEmail={userEmail}
                  onLogout={() => setLoggedIn(false)}
                  chats={chats}
                  onUpdateChats={setChats}
                  activeChatId={activeChatId}
                  onSelectChat={setActiveChatId}
                  runningMap={runningMap}
                  onStartPrompt={handleStartPrompt}
                  scopeTools={scopeTools}
                  onUpdateScopeTools={setScopeTools}
                />
              )}
            </Stack.Screen>
            <Stack.Screen
              name="Session"
              component={SessionScreen}
              options={{ presentation: 'card', gestureEnabled: true }}
            />
          </Stack.Navigator>
        </NavigationContainer>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  )
}
