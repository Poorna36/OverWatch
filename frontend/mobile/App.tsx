import React from 'react'
import { View, Text, StyleSheet } from 'react-native'
import { NavigationContainer, DefaultTheme } from '@react-navigation/native'
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs'
import { createStackNavigator } from '@react-navigation/stack'
import { HomeScreen } from './screens/HomeScreen'
import { NewTaskScreen } from './screens/NewTaskScreen'
import { SessionScreen } from './screens/SessionScreen'
import { AuditScreen } from './screens/AuditScreen'
import { SettingsScreen } from './screens/SettingsScreen'
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

// ─── Custom tab bar icon ──────────────────────────────────────────────────────
function TabIcon({
  symbol, label, focused,
}: { symbol: string; label: string; focused: boolean }) {
  return (
    <View style={[ti.wrap, focused && ti.wrapFocused]}>
      <Text style={[ti.icon, focused && ti.iconFocused]}>{symbol}</Text>
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
  icon: { fontSize: 16, color: T.textFaint },
  iconFocused: { color: T.indigo },
  label: { fontSize: 10, color: T.textFaint, fontWeight: '600', letterSpacing: 0.3 },
  labelFocused: { color: T.indigo },
})

// ─── Main tabs ────────────────────────────────────────────────────────────────
function MainTabs({ navigation }: { navigation: any }) {
  return (
    <Tab.Navigator
      screenOptions={{
        headerShown: false,
        tabBarStyle: {
          backgroundColor: T.surface,
          borderTopWidth: 1,
          borderTopColor: T.border,
          height: 72,
          paddingBottom: 10,
          paddingTop: 8,
        },
        tabBarShowLabel: false,
      }}
    >
      <Tab.Screen
        name="HomeTab"
        options={{
          tabBarIcon: ({ focused }) => <TabIcon symbol="⊙" label="Home" focused={focused} />,
        }}
      >
        {(props) => (
          <HomeScreen
            {...props}
            navigation={{
              ...props.navigation,
              navigate: (name: string, params?: any) => navigation.navigate(name, params),
            }}
          />
        )}
      </Tab.Screen>

      <Tab.Screen
        name="NewTaskTab"
        options={{
          tabBarIcon: ({ focused }) => <TabIcon symbol="⊕" label="New" focused={focused} />,
        }}
      >
        {(props) => (
          <NewTaskScreen
            {...props}
            navigation={{
              ...props.navigation,
              navigate: (name: string, params?: any) => navigation.navigate(name, params),
              goBack: () => props.navigation.goBack(),
            }}
          />
        )}
      </Tab.Screen>

      <Tab.Screen
        name="AuditTab"
        options={{
          tabBarIcon: ({ focused }) => <TabIcon symbol="≡" label="Audit" focused={focused} />,
        }}
      >
        {(props) => <AuditScreen {...props} navigation={props.navigation} />}
      </Tab.Screen>

      <Tab.Screen
        name="SettingsTab"
        component={SettingsScreen}
        options={{
          tabBarIcon: ({ focused }) => <TabIcon symbol="⚙" label="Settings" focused={focused} />,
        }}
      />
    </Tab.Navigator>
  )
}

// ─── Root stack ───────────────────────────────────────────────────────────────
export default function App() {
  return (
    <NavigationContainer theme={Theme}>
      <Stack.Navigator
        screenOptions={{
          headerShown: false,
          cardStyle: { backgroundColor: T.bg },
          gestureEnabled: true,
        }}
      >
        <Stack.Screen name="Main" component={MainTabs} />
        <Stack.Screen
          name="Session"
          component={SessionScreen}
          options={{ presentation: 'card', gestureEnabled: true }}
        />
      </Stack.Navigator>
    </NavigationContainer>
  )
}
