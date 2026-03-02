'use client';

import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  IconButton,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { ResizableDrawer } from './ResizableDrawer';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline';

const GOLD = '#D4A843';
const GREEN = '#4CAF50';
const RED = '#F44336';
const DRAWER_WIDTH = 500;

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface QualityIssue {
  table: string;
  column?: string;
  detail: string;
}

interface QualityCheckSection {
  title: string;
  issues: QualityIssue[];
}

interface TableQualitySummary {
  tableName: string;
  rowCount: number;
  issueCount: number;
  score: number;
}

type QualityBand = 'good' | 'attention' | 'poor';

interface QualityReport {
  overallScore: number;
  qualityBand?: QualityBand;
  qualityLabel?: string;
  totalTables: number;
  passingTables: number;
  checks: QualityCheckSection[];
  tableSummaries: TableQualitySummary[];
}

interface DataQualityReportProps {
  open: boolean;
  onClose: () => void;
  report: QualityReport | null;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function getBand(report: QualityReport): { band: QualityBand; label: string } {
  if (report.qualityBand && report.qualityLabel) {
    return { band: report.qualityBand, label: report.qualityLabel };
  }
  // Fallback for older reports without band info
  if (report.overallScore >= 100) return { band: 'good', label: 'Good Quality' };
  if (report.overallScore >= 60) return { band: 'attention', label: 'Needs Attention' };
  return { band: 'poor', label: 'Poor Quality' };
}

function getBandColor(band: QualityBand): string {
  if (band === 'good') return GREEN;
  if (band === 'attention') return GOLD;
  return RED;
}

function getBandIcon(band: QualityBand): React.ReactNode {
  const sx = { fontSize: 48 };
  if (band === 'good') return <CheckCircleOutlineIcon sx={{ ...sx, color: GREEN }} />;
  if (band === 'attention') return <WarningAmberIcon sx={{ ...sx, color: GOLD }} />;
  return <ErrorOutlineIcon sx={{ ...sx, color: RED }} />;
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function DataQualityReport({
  open,
  onClose,
  report,
}: DataQualityReportProps): React.ReactNode {
  const { band, label } = report ? getBand(report) : { band: 'good' as QualityBand, label: 'Good Quality' };
  const color = getBandColor(band);

  return (
    <ResizableDrawer
      defaultWidth={DRAWER_WIDTH}
      open={open}
      onClose={onClose}
    >
      <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        {/* Header */}
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            px: 2.5,
            py: 2,
            borderBottom: 1,
            borderColor: 'divider',
          }}
        >
          <Typography variant="h6" fontWeight={700}>
            Data Quality Report
          </Typography>
          <IconButton onClick={onClose} size="small">
            <CloseIcon fontSize="small" />
          </IconButton>
        </Box>

        {/* Scrollable content */}
        <Box sx={{ flex: 1, overflow: 'auto', px: 2.5, py: 2 }}>
          {report === null ? (
            <Typography variant="body2" color="text.secondary" sx={{ textAlign: 'center', mt: 4 }}>
              No report data available.
            </Typography>
          ) : (
            <>
              {/* Quality band indicator */}
              <Box
                sx={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  mb: 3,
                  mt: 1,
                }}
              >
                {getBandIcon(band)}
                <Typography
                  sx={{
                    fontSize: 22,
                    fontWeight: 700,
                    color,
                    mt: 1,
                  }}
                >
                  {label}
                </Typography>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ mt: 0.5 }}
                >
                  {report.totalTables} table{report.totalTables !== 1 ? 's' : ''} analyzed
                </Typography>
              </Box>

              {/* Collapsible check sections */}
              {report.checks?.map((section) => (
                <Accordion
                  key={section.title}
                  disableGutters
                  elevation={0}
                  sx={{
                    bgcolor: 'background.paper',
                    border: 1,
                    borderColor: 'divider',
                    '&:before': { display: 'none' },
                    mb: 1,
                    borderRadius: '8px !important',
                    overflow: 'hidden',
                  }}
                >
                  <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, width: '100%' }}>
                      <Typography variant="body2" fontWeight={600}>
                        {section.title}
                      </Typography>
                      <Typography
                        variant="caption"
                        sx={{
                          ml: 'auto',
                          mr: 1,
                          color: section.issues.length > 0 ? GOLD : GREEN,
                          fontWeight: 600,
                        }}
                      >
                        {section.issues.length === 0
                          ? 'Passed'
                          : `${section.issues.length} issue${section.issues.length !== 1 ? 's' : ''}`}
                      </Typography>
                    </Box>
                  </AccordionSummary>
                  <AccordionDetails sx={{ pt: 0 }}>
                    {section.issues.length === 0 ? (
                      <Typography variant="caption" color="text.secondary">
                        No issues detected.
                      </Typography>
                    ) : (
                      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
                        {section.issues.map((issue, idx) => (
                          <Box
                            key={`${issue.table}-${issue.column ?? ''}-${idx}`}
                            sx={{ pl: 1, borderLeft: 2, borderColor: GOLD }}
                          >
                            <Typography variant="caption" fontWeight={600}>
                              {issue.table}
                              {issue.column ? ` . ${issue.column}` : ''}
                            </Typography>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              sx={{ display: 'block' }}
                            >
                              {issue.detail}
                            </Typography>
                          </Box>
                        ))}
                      </Box>
                    )}
                  </AccordionDetails>
                </Accordion>
              ))}

              {/* Per-table summary */}
              {report.tableSummaries && report.tableSummaries.length > 0 && (
                <Box sx={{ mt: 3 }}>
                  <Typography variant="subtitle2" fontWeight={700} sx={{ mb: 1.5 }}>
                    Per-Table Summary
                  </Typography>
                  <TableContainer
                    sx={{
                      border: 1,
                      borderColor: 'divider',
                      borderRadius: 1,
                      overflow: 'hidden',
                    }}
                  >
                    <Table size="small">
                      <TableHead>
                        <TableRow>
                          <TableCell sx={{ fontWeight: 700 }}>Table Name</TableCell>
                          <TableCell align="right" sx={{ fontWeight: 700 }}>Rows</TableCell>
                          <TableCell align="right" sx={{ fontWeight: 700 }}>Issues</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {report.tableSummaries.map((ts) => (
                          <TableRow key={ts.tableName}>
                            <TableCell>
                              <Typography variant="body2">{ts.tableName}</Typography>
                            </TableCell>
                            <TableCell align="right">
                              <Typography variant="body2">
                                {ts.rowCount.toLocaleString()}
                              </Typography>
                            </TableCell>
                            <TableCell align="right">
                              <Typography
                                variant="body2"
                                fontWeight={600}
                                sx={{ color: ts.issueCount > 0 ? GOLD : GREEN }}
                              >
                                {ts.issueCount === 0 ? 'None' : ts.issueCount}
                              </Typography>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </TableContainer>
                </Box>
              )}
            </>
          )}
        </Box>
      </Box>
    </ResizableDrawer>
  );
}

export type {
  QualityReport,
  QualityBand,
  QualityIssue,
  QualityCheckSection,
  TableQualitySummary,
  DataQualityReportProps,
};
