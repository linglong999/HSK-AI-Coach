import { NavLink, Route, Routes } from 'react-router-dom'
import Home from './pages/Home'
import Speak from './pages/Speak'
import Review from './pages/Review'
import Help from './pages/Help'

export default function App() {
  return (
    <div className="app">
      <nav className="topnav">
        <span className="brand">汉语 AI 教练</span>
        <NavLink to="/" end>图谱</NavLink>
        <NavLink to="/speak">开口说</NavLink>
        <NavLink to="/review">复习</NavLink>
        <NavLink to="/help">帮助</NavLink>
      </nav>
      <main className="content">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/speak" element={<Speak />} />
          <Route path="/review" element={<Review />} />
          <Route path="/help" element={<Help />} />
        </Routes>
      </main>
    </div>
  )
}