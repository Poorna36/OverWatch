import React, { useState } from 'react'
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
import { DEFAULT_ENV, MOCK_CHATS, type ChatThread, type EnvConfig } from './lib/mock-data'
import { T } from './components/ui'

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
}: { code: 'chat' | 'home' | 'tasks' | 'settings'; label: string; focused: boolean }) {
  const glyph = { chat: '>', home: '~', tasks: '=', settings: '*' }[code]
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
            env={env}
            userEmail={userEmail}
            navigation={props.navigation}
          />
        )}
      </Tab.Screen>

      {/* 2. Home */}
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

      {/* 3. Tasks & Permissions */}
      <Tab.Screen
        name="TasksTab"
        options={{
          tabBarIcon: ({ focused }) => <TabIcon code="tasks" label="Tasks" focused={focused} />,
        }}
      >
        {(props) => (
          <TasksScreen
            navigation={{
              ...props.navigation,
              navigate: (name: string, params?: any) => navigation.navigate(name, params),
            }}
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
