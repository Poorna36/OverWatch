import React from 'react'
import { NavigationContainer, DefaultTheme } from '@react-navigation/native'
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs'
import { createStackNavigator } from '@react-navigation/stack'
import { View, Text, StyleSheet } from 'react-native'
import { HomeScreen } from './screens/HomeScreen'
import { NewTaskScreen } from './screens/NewTaskScreen'
import { SessionScreen } from './screens/SessionScreen'
import { AuditScreen } from './screens/AuditScreen'
import { SettingsScreen } from './screens/SettingsScreen'
import { C } from './components/ui'

// ─── Navigators ───────────────────────────────────────────────────────────────
const Tab = createBottomTabNavigator()
const Stack = createStackNavigator()

// Theme
const NavTheme = {
  ...DefaultTheme,
  colors: {
    ...DefaultTheme.colors,
    background: C.bg,
    card: C.surface,
    text: C.text,
    border: C.border,
    primary: C.accent,
  },
}

// ─── Tab icon SVGs (inline text approximations — no emoji) ────────────────────
function TabIcon({ icon, focused }: { icon: string; focused: boolean }) {
  return (
    <Text style={[ts.icon, focused && ts.iconFocused]}>{icon}</Text>
  )
}

// ─── Main tab navigator ───────────────────────────────────────────────────────
function MainTabs({ navigation }: { navigation: any }) {
  return (
    <Tab.Navigator
      screenOptions={{
        headerShown: false,
        tabBarStyle: ts.tabBar,
        tabBarActiveTintColor: C.accent,
        tabBarInactiveTintColor: C.textSubtle,
        tabBarLabelStyle: ts.tabLabel,
        tabBarItemStyle: ts.tabItem,
      }}
    >
      <Tab.Screen
        name="HomeTab"
        options={{
          title: 'Home',
          tabBarIcon: ({ focused }) => <TabIcon icon="⊙" focused={focused} />,
        }}
      >
        {(props) => <HomeScreen {...props} navigation={{ ...props.navigation, navigate: (name: string, params?: any) => navigation.navigate(name, params) }} />}
      </Tab.Screen>

      <Tab.Screen
        name="NewTaskTab"
        options={{
          title: 'New Task',
          tabBarIcon: ({ focused }) => <TabIcon icon="⊕" focused={focused} />,
        }}
      >
        {(props) => <NewTaskScreen {...props} navigation={{ ...props.navigation, navigate: (name: string, params?: any) => navigation.navigate(name, params), goBack: props.navigation.goBack }} />}
      </Tab.Screen>

      <Tab.Screen
        name="AuditTab"
        component={AuditScreen}
        options={{
          title: 'Audit',
          tabBarIcon: ({ focused }) => <TabIcon icon="≡" focused={focused} />,
        }}
      />

      <Tab.Screen
        name="SettingsTab"
        component={SettingsScreen}
        options={{
          title: 'Settings',
          tabBarIcon: ({ focused }) => <TabIcon icon="⚙" focused={focused} />,
        }}
      />
    </Tab.Navigator>
  )
}

// ─── Root stack (tabs + session modal) ───────────────────────────────────────
export default function App() {
  return (
    <NavigationContainer theme={NavTheme}>
      <Stack.Navigator screenOptions={{ headerShown: false, cardStyle: { backgroundColor: C.bg } }}>
        <Stack.Screen name="Main" component={MainTabs} />
        <Stack.Screen
          name="Session"
          component={SessionScreen}
          options={{ gestureEnabled: true, presentation: 'card' }}
        />
      </Stack.Navigator>
    </NavigationContainer>
  )
}

// ─── Tab bar styles ───────────────────────────────────────────────────────────
const ts = StyleSheet.create({
  tabBar: {
    backgroundColor: C.surface,
    borderTopWidth: 1,
    borderTopColor: C.border,
    paddingTop: 6,
    paddingBottom: 4,
    height: 60,
  },
  tabLabel: {
    fontSize: 10,
    fontWeight: '600',
    marginTop: 2,
  },
  tabItem: {
    paddingTop: 4,
  },
  icon: {
    fontSize: 18,
    color: C.textSubtle,
  },
  iconFocused: {
    color: C.accent,
  },
})
