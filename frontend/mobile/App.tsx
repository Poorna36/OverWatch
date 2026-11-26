import React from 'react'
import { StyleSheet, Text, View } from 'react-native'
import { GestureHandlerRootView } from 'react-native-gesture-handler'
import { SafeAreaProvider, useSafeAreaInsets } from 'react-native-safe-area-context'
import { DefaultTheme, NavigationContainer } from '@react-navigation/native'
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs'
import { createStackNavigator } from '@react-navigation/stack'
import { Feather } from '@expo/vector-icons'
import { StatusBar } from 'expo-status-bar'

import { C } from './components/ui'
import { StoreProvider } from './lib/store'
import { ToastProvider } from './lib/toast'
import { HomeScreen } from './screens/HomeScreen'
import { NewTaskScreen } from './screens/NewTaskScreen'
import { PlanPreviewScreen } from './screens/PlanPreviewScreen'
import { SessionScreen } from './screens/SessionScreen'
import { AuditScreen } from './screens/AuditScreen'
import { SettingsScreen } from './screens/SettingsScreen'

const Tab = createBottomTabNavigator()
const Stack = createStackNavigator()

const NavTheme = {
  ...DefaultTheme,
  colors: {
    ...DefaultTheme.colors,
    background: C.background,
    card: C.surface,
    text: C.foreground,
    border: C.border,
    primary: C.primary,
    notification: C.primary,
  },
}

function TabIcon({
  icon,
  label,
  focused,
  isNew,
}: {
  icon: keyof typeof Feather.glyphMap
  label: string
  focused: boolean
  isNew?: boolean
}) {
  return (
    <View style={ti.wrap}>
      {isNew ? (
        <View style={ti.newBubble}>
          <Feather name={icon} size={18} color={C.primaryFg} />
        </View>
      ) : (
        <Feather name={icon} size={19} color={focused ? C.primary : C.mutedFg} />
      )}
      <Text style={[ti.label, { color: focused ? C.primary : C.mutedFg }]}>{label}</Text>
    </View>
  )
}

const ti = StyleSheet.create({
  wrap: { alignItems: 'center', justifyContent: 'center', gap: 3 },
  newBubble: {
    width: 34,
    height: 34,
    borderRadius: 17,
    backgroundColor: C.primary,
    alignItems: 'center',
    justifyContent: 'center',
  },
  label: { fontSize: 11, fontWeight: '600' },
})

function MainTabs() {
  const insets = useSafeAreaInsets()
  return (
    <Tab.Navigator
      initialRouteName="SessionsTab"
      screenOptions={{
        headerShown: false,
        tabBarShowLabel: false,
        tabBarStyle: {
          backgroundColor: C.surface,
          borderTopWidth: 1,
          borderTopColor: C.border,
          height: 64 + insets.bottom,
          paddingBottom: Math.max(8, insets.bottom),
          paddingTop: 6,
        },
      }}
    >
      <Tab.Screen
        name="SessionsTab"
        component={HomeScreen}
        options={{
          tabBarIcon: ({ focused }) => <TabIcon icon="home" label="Sessions" focused={focused} />,
        }}
      />
      <Tab.Screen
        name="NewTaskTab"
        component={NewTaskScreen}
        options={{
          tabBarIcon: ({ focused }) => <TabIcon icon="plus" label="New task" focused={focused} isNew />,
        }}
      />
      <Tab.Screen
        name="AuditTab"
        component={AuditScreen}
        options={{
          tabBarIcon: ({ focused }) => <TabIcon icon="file-text" label="Audit" focused={focused} />,
        }}
      />
      <Tab.Screen
        name="SettingsTab"
        component={SettingsScreen}
        options={{
          tabBarIcon: ({ focused }) => <TabIcon icon="settings" label="Settings" focused={focused} />,
        }}
      />
    </Tab.Navigator>
  )
}

export default function App() {
  return (
    <GestureHandlerRootView style={{ flex: 1, backgroundColor: C.background }}>
      <SafeAreaProvider>
        <StatusBar style="light" />
        <StoreProvider>
          <ToastProvider>
            <NavigationContainer theme={NavTheme}>
              <Stack.Navigator
                screenOptions={{
                  headerShown: false,
                  cardStyle: { backgroundColor: C.background },
                  gestureEnabled: true,
                }}
              >
                <Stack.Screen name="MainTabs" component={MainTabs} />
                <Stack.Screen name="PlanPreview" component={PlanPreviewScreen} />
                <Stack.Screen name="Session" component={SessionScreen} />
              </Stack.Navigator>
            </NavigationContainer>
          </ToastProvider>
        </StoreProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  )
}
