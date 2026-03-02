/**
 * Engineering Structure Configuration
 * Hierarchical configuration for Engineering section and its sub-disciplines
 * This provides a flexible, soft-coded structure for engineering modules
 */

import {
  BeakerIcon,
  WrenchIcon,
  CpuChipIcon,
  BoltIcon,
  HomeModernIcon,
  CogIcon,
  SparklesIcon,
  DocumentTextIcon,
  TableCellsIcon,
  DocumentChartBarIcon,
  CircleStackIcon
} from '@heroicons/react/24/outline'

/**
 * Engineering Disciplines Configuration
 * Each discipline can have multiple sub-features
 */
export const ENGINEERING_DISCIPLINES = {
  process: {
    id: 'process',
    name: 'Process',
    fullName: 'Process Engineering',
    icon: BeakerIcon,
    description: 'Process flow diagrams and engineering',
    color: 'blue',
    gradient: 'from-blue-500 to-blue-600',
    order: 1,
    subFeatures: [
      {
        id: 'pid',
        name: 'P&ID',
        fullName: 'Piping & Instrumentation Diagram',
        icon: DocumentChartBarIcon,
        path: '/pid/upload',
        description: 'AI-powered P&ID design verification',
        moduleCode: 'pid_analysis',
        badge: 'AI'
      },
      {
        id: 'pfdVerification',
        name: 'PFD Verification',
        fullName: 'Process Flow Diagram Verification',
        icon: DocumentTextIcon,
        path: '/designiq/pfd-verification',
        description: 'AI-powered PFD design verification with reference documents',
        moduleCode: 'designiq',
        badge: 'NEW'
      },
      {
        id: 'processDataSheet',
        name: 'Datasheets',
        fullName: 'Process Datasheets',
        icon: DocumentTextIcon,
        path: '/engineering/process/datasheet',
        description: 'Process equipment datasheets',
        moduleCode: 'process_datasheet',
        badge: 'New'
      }
    ,      {        id: 'lineList',
        name: 'Line List',
        fullName: 'Line List - Base Extraction',
        icon: TableCellsIcon,
        path: '/engineering/process/line-list',
        description: 'Extract 8 base columns from P&ID (P&ID-only, no enrichment)',
        moduleCode: 'pid_analysis'
      }
    ]
  },

  piping: {
    id: 'piping',
    name: 'Piping',
    fullName: 'Piping Engineering',
    icon: WrenchIcon,
    description: 'Critical line management and piping specifications',
    color: 'orange',
    gradient: 'from-orange-500 to-orange-600',
    order: 2,
    subFeatures: [
      {
        id: 'criticalLineList',
        name: 'Critical Line List',
        fullName: 'Critical Line List - Full Enrichment',
        icon: TableCellsIcon,
        path: '/engineering/piping/critical-line-list',
        description: '5-document upload (P&ID+PFD+HMB+PMS+NACE) for full 35-column extraction with enrichment',
        moduleCode: 'designiq',
        badge: 'FULL'
      },
      {
        id: 'pms',
        name: 'PMS',
        fullName: 'Piping Material Specification',
        icon: TableCellsIcon,
        path: '/engineering/piping/pms',
        description: 'Piping material specification management',
        moduleCode: 'piping_pms',
        badge: 'New'
      },
      {
        id: 'pipingDataSheet',
        name: 'Datasheets',
        fullName: 'Piping Datasheets',
        icon: DocumentTextIcon,
        path: '/engineering/piping/datasheet',
        description: 'Piping component datasheets',
        moduleCode: 'piping_datasheet',
      }
    ]
  },

  instrument: {
    id: 'instrument',
    name: 'Instrument',
    fullName: 'Instrumentation Engineering',
    icon: CpuChipIcon,
    description: 'Instrumentation and control systems',
    color: 'purple',
    gradient: 'from-purple-500 to-purple-600',
    order: 3,
    subFeatures: [
      {
        id: 'instrumentIndex',
        name: 'Instrument Index',
        fullName: 'Instrument Index',
        icon: TableCellsIcon,
        path: '/engineering/instrument/index',
        description: 'Comprehensive instrument index management',
        moduleCode: 'instrument_index',
        badge: 'New'
      },
      {
        id: 'instrumentDataSheet',
        name: 'Datasheets',
        fullName: 'Instrument Datasheets',
        icon: DocumentTextIcon,
        path: '/engineering/instrument/datasheet',
        description: 'Instrument specification datasheets',
        moduleCode: 'instrument_datasheet',
        badge: 'New'
      }
    ]
  },

  electrical: {
    id: 'electrical',
    name: 'Electrical',
    fullName: 'Electrical Engineering',
    icon: BoltIcon,
    description: 'Electrical systems and power distribution',
    color: 'yellow',
    gradient: 'from-yellow-500 to-yellow-600',
    order: 4,
    subFeatures: [
      {
        id: 'sld',
        name: 'SLD',
        fullName: 'Single Line Diagram',
        icon: DocumentChartBarIcon,
        path: '/engineering/electrical/sld',
        description: 'Single line diagram design and analysis',
        moduleCode: 'electrical_sld',
        badge: 'New'
      },
      {
        id: 'electricalDatasheets',
        name: 'Datasheets',
        fullName: 'Electrical Datasheets',
        icon: DocumentTextIcon,
        path: '/engineering/electrical/datasheet',
        description: '27 electrical engineering initiatives - Datasheets, diagrams, layouts, schedules',
        moduleCode: 'electrical_datasheet',
        badge: 'New'
      }
    ]
  },

  civil: {
    id: 'civil',
    name: 'Civil',
    fullName: 'Civil Engineering',
    icon: HomeModernIcon,
    description: 'Civil and structural engineering',
    color: 'gray',
    gradient: 'from-gray-500 to-gray-600',
    order: 5,
    subFeatures: [
      {
        id: 'civilDataSheet',
        name: 'Datasheets',
        fullName: 'Civil Datasheets',
        icon: DocumentTextIcon,
        path: '/engineering/civil/datasheet',
        description: 'Civil and structural datasheets',
        moduleCode: 'civil_datasheet',
        badge: 'New'
      }
    ]
  },

  mechanical: {
    id: 'mechanical',
    name: 'Mechanical',
    fullName: 'Mechanical Engineering',
    icon: CogIcon,
    description: 'Mechanical equipment and systems',
    color: 'indigo',
    gradient: 'from-indigo-500 to-indigo-600',
    order: 6,
    subFeatures: [
      {
        id: 'mechanicalDataSheet',
        name: 'Datasheets',
        fullName: 'Mechanical Datasheets',
        icon: DocumentTextIcon,
        path: '/engineering/mechanical/datasheet',
        description: 'Mechanical equipment datasheets',
        moduleCode: 'mechanical_datasheet',
        badge: 'New'
      }
    ]
  },

  digitization: {
    id: 'digitization',
    name: 'Digitization',
    fullName: 'Digital Transformation',
    icon: SparklesIcon,
    description: 'Digital tools and automation',
    color: 'pink',
    gradient: 'from-pink-500 to-pink-600',
    order: 7,
    subFeatures: [
      {
        id: 'specCustomization',
        name: 'Spec Customization',
        fullName: 'Specification Customization',
        icon: CircleStackIcon,
        path: '/engineering/digitization/spec-customization',
        description: 'AI-powered spec generation and customization',
        moduleCode: 'spec_customization',
        badge: 'AI'
      },
      {
        id: 'digitizationDatasheet',
        name: 'Datasheets',
        fullName: 'Digitization Datasheets',
        icon: DocumentTextIcon,
        path: '/engineering/digitization/datasheet',
        description: 'Digital transformation datasheets and documentation',
        moduleCode: 'digitization_datasheet',
        badge: 'New'
      }
    ]
  }
}

/**
 * Legacy features to maintain backward compatibility
 * These map to the old structure
 */
export const LEGACY_ENGINEERING_FEATURES = {
  designiq: {
    id: 'designiq',
    name: 'DesignIQ',
    fullName: 'DesignIQ - AI Design Assistant',
    icon: SparklesIcon,
    path: '/designiq',
    description: 'AI-powered design optimization',
    moduleCode: 'designiq',
    badge: 'AI',
    showInLegacy: true
  },
  pfdConverter: {
    id: 'pfd',
    name: 'PFD Converter',
    fullName: 'PFD to P&ID Converter',
    icon: DocumentChartBarIcon,
    path: '/pfd/upload',
    description: 'Intelligent PFD conversion',
    moduleCode: 'pfd_to_pid',
    badge: 'AI',
    showInLegacy: true
  }
}

/**
 * Get all engineering disciplines sorted by order
 */
export const getEngineeringDisciplines = () => {
  return Object.values(ENGINEERING_DISCIPLINES).sort((a, b) => a.order - b.order)
}

/**
 * Get a specific discipline by ID
 */
export const getDiscipline = (disciplineId) => {
  return ENGINEERING_DISCIPLINES[disciplineId]
}

/**
 * Get all sub-features for a discipline
 */
export const getDisciplineSubFeatures = (disciplineId) => {
  const discipline = ENGINEERING_DISCIPLINES[disciplineId]
  return discipline ? discipline.subFeatures : []
}

/**
 * Find a sub-feature by path
 */
export const findSubFeatureByPath = (path) => {
  for (const discipline of Object.values(ENGINEERING_DISCIPLINES)) {
    const subFeature = discipline.subFeatures.find(sf => sf.path === path)
    if (subFeature) {
      return { discipline, subFeature }
    }
  }
  return null
}

/**
 * Get color classes for a discipline
 */
export const getDisciplineColors = (disciplineId) => {
  const discipline = ENGINEERING_DISCIPLINES[disciplineId]
  if (!discipline) return {}

  const colorMap = {
    blue: {
      bg: 'bg-blue-50',
      text: 'text-blue-700',
      border: 'border-blue-200',
      hover: 'hover:bg-blue-100'
    },
    orange: {
      bg: 'bg-orange-50',
      text: 'text-orange-700',
      border: 'border-orange-200',
      hover: 'hover:bg-orange-100'
    },
    purple: {
      bg: 'bg-purple-50',
      text: 'text-purple-700',
      border: 'border-purple-200',
      hover: 'hover:bg-purple-100'
    },
    yellow: {
      bg: 'bg-yellow-50',
      text: 'text-yellow-700',
      border: 'border-yellow-200',
      hover: 'hover:bg-yellow-100'
    },
    gray: {
      bg: 'bg-gray-50',
      text: 'text-gray-700',
      border: 'border-gray-200',
      hover: 'hover:bg-gray-100'
    },
    indigo: {
      bg: 'bg-indigo-50',
      text: 'text-indigo-700',
      border: 'border-indigo-200',
      hover: 'hover:bg-indigo-100'
    },
    pink: {
      bg: 'bg-pink-50',
      text: 'text-pink-700',
      border: 'border-pink-200',
      hover: 'hover:bg-pink-100'
    }
  }

  return colorMap[discipline.color] || colorMap.blue
}

/**
 * Check if user has access to a discipline or sub-feature
 */
export const hasAccess = (moduleCode, userModules, isAdmin) => {
  if (isAdmin) return true
  if (!moduleCode) return true
  return userModules.includes(moduleCode)
}

export default ENGINEERING_DISCIPLINES






