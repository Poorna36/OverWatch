import React from 'react'
import { NavigationContainer, DefaultTheme } from '@react-navigation/native'
import { createStackNavigator } from '@react-navigation/stack'
import { HomeScreen } from './screens/HomeScreen'
import { NewTaskScreen } from './screens/NewTaskScreen'
import { SessionScreen } from './screens/SessionScreen'
import { AuditScreen } from './screens/AuditScreen'
import { C } from './components/ui'

const Stack = createStackNavigator()

const Theme = {
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

export default function App() {
  return (
    <NavigationContainer theme={Theme}>
      <Stack.Navigator
        screenOptions={{
          headerShown: false,
          cardStyle: { backgroundColor: C.bg },
          gestureEnabled: true,
        }}
      >
        <Stack.Screen name="Home" component={HomeScreen} />
        <Stack.Screen name="NewTask" component={NewTaskScreen} />
        <Stack.Screen name="Session" component={SessionScreen} />
        <Stack.Screen name="Audit" component={AuditScreen} />
      </Stack.Navigator>
    </NavigationContainer>
  )
}
