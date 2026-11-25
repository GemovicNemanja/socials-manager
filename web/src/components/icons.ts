import {
  Calendar,
  ChartSpline,
  Check,
  ChevronDown,
  ChevronsUpDown,
  Clock3,
  Columns3,
  Download,
  FlaskConical,
  LayoutDashboard,
  Plus,
  Rows3,
  Search,
  SlidersHorizontal,
  UserRound,
  X,
  type LucideIcon,
} from "lucide-react";

/** Canvas icon names (code names on the Wonder canvas) to their Lucide glyphs. */
export const icons = {
  NavOverview: LayoutDashboard,
  NavPosts: Rows3,
  NavAccount: UserRound,
  NavForecasts: ChartSpline,
  NavExperiments: FlaskConical,
  ActionSearch: Search,
  ActionColumns: Columns3,
  ActionDownload: Download,
  ActionExpand: ChevronDown,
  ActionSwitch: ChevronsUpDown,
  ActionCheck: Check,
  ActionAdd: Plus,
  ActionClose: X,
  ActionCalendar: Calendar,
  ActionFilter: SlidersHorizontal,
  ActorSystem: Clock3,
} satisfies Record<string, LucideIcon>;

export type IconName = keyof typeof icons;
export type NavIconName = Extract<IconName, `Nav${string}`>;
